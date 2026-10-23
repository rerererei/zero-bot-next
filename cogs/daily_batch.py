# cogs/daily_batch.py
"""
Bot全体の日次バッチ（rainbowl_server_handover.md 12章）。

cronは使わず、常駐Bot内の`tasks.loop`でJST 0:00に1日1回発火させる。
Bot起動直後にも1回実行し、停止中に取りこぼした分へ追いつく。
各ジョブは冪等（何度実行しても同じ結果）にしてあるため、
多重発火・手動実行（/zbadmin run_daily_batch）と重なっても副作用は出ない。

現在のジョブ（rainbowl設定を持つギルドのみ）：
- プロフィール未提出3日自動キック
- 月次活動確認の確認期限処理（残留確定していない人のキック。毎日チェック）
- 月次活動確認の集計（前月分。毎月1日に実処理、以降の日は処理済みガードで何もしない）
"""

import asyncio
from datetime import time, timedelta, timezone
from typing import Dict, List

import discord
from discord.ext import commands, tasks

from services.rainbowl_config_service import (
    RainbowlConfigError,
    RainbowlConfigNotFoundError,
    get_rainbowl_config,
)
from services.rainbowl_activity_review_service import (
    get_activity_review_config,
    process_review_deadlines,
    run_monthly_review,
)
from services.rainbowl_onboarding_service import (
    process_profile_missing_auto_kick,
)


JST = timezone(timedelta(hours=9))


class DailyBatch(commands.Cog):
    """日次バッチ（JST 0:00）"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._lock = asyncio.Lock()

        self.daily_loop.start()
        print("[DailyBatch] daily_loop started")

    async def cog_load(self) -> None:
        # 起動直後の追いつき実行（on_ready後に1回だけ）
        asyncio.create_task(self._run_on_startup())

    def cog_unload(self):
        self.daily_loop.cancel()

    async def _run_on_startup(self) -> None:
        await self.bot.wait_until_ready()
        await self.run_all()

    @tasks.loop(time=time(hour=0, minute=0, tzinfo=JST))
    async def daily_loop(self):
        await self.run_all()

    @daily_loop.before_loop
    async def before_daily_loop(self):
        await self.bot.wait_until_ready()

    async def run_all(self) -> Dict[int, List[str]]:
        """
        全ジョブを全ギルドに対して実行し、ギルドIDごとの結果サマリー行を返す。
        実行中に再度呼ばれた場合は、前の実行の完了を待ってから実行する。
        """
        async with self._lock:
            print("[DailyBatch] 日次バッチ開始")
            results: Dict[int, List[str]] = {}

            for guild in self.bot.guilds:
                lines = await self._run_rainbowl_jobs(guild)

                if lines:
                    results[guild.id] = lines

            summary = " / ".join(
                f"{self.bot.get_guild(guild_id).name}: {line}"
                for guild_id, lines in results.items()
                for line in lines
            )
            print(
                "[DailyBatch] 日次バッチ完了: "
                + (summary or "処理対象なし")
            )
            return results

    async def _run_rainbowl_jobs(
        self,
        guild: discord.Guild,
    ) -> List[str]:
        try:
            config = await get_rainbowl_config(guild.id)
        except RainbowlConfigNotFoundError:
            return []
        except RainbowlConfigError as exc:
            print(
                "[DailyBatch] rainbowl設定取得エラー:"
                f" guild_id={guild.id} error={exc}"
            )
            return ["rainbowl設定取得エラー"]

        results: List[str] = []

        try:
            kicked = await process_profile_missing_auto_kick(
                guild,
                config,
                self.bot,
            )
            results.append(f"プロフィール未提出自動キック {kicked}件")
        except Exception as exc:
            print(
                "[DailyBatch] プロフィール未提出自動キックに失敗しました:"
                f" guild_id={guild.id} error={exc}"
            )
            results.append("プロフィール未提出自動キック 失敗")

        review_config = await get_activity_review_config(guild.id)

        if review_config is None:
            return results

        # 期限処理を先に行う（前月サイクルを締めてから今月の集計を始める）
        try:
            summary = await process_review_deadlines(guild, review_config)
            if summary:
                results.append(summary)
        except Exception as exc:
            print(
                "[DailyBatch] 活動確認の期限処理に失敗しました:"
                f" guild_id={guild.id} error={exc}"
            )
            results.append("活動確認の期限処理 失敗")

        try:
            summary = await run_monthly_review(guild, config, review_config)
            if summary:
                results.append(summary)
        except Exception as exc:
            print(
                "[DailyBatch] 活動確認の月次集計に失敗しました:"
                f" guild_id={guild.id} error={exc}"
            )
            results.append("活動確認の月次集計 失敗")

        return results


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DailyBatch(bot))
