# cogs/rainbowl_profile_gate.py
"""
rainbowl機能：男性/女性プロフィールチャンネルへの初回投稿検知。

「了解しました」ボタンで了承済みロールになったユーザーが、
自分の性別のプロフィールチャンネルへ最初に投稿した時点で、
了承済みロールを新人ロールへ切り替え、本人専用チャンネルを削除する。

判定・DynamoDB操作・チャンネル操作は
services/rainbowl_onboarding_service.py に委譲する。
"""

from typing import Optional

import discord
from discord.ext import commands

from services.rainbowl_config_service import (
    RainbowlConfigError,
    RainbowlConfigNotFoundError,
    RainbowlGuildConfig,
    get_rainbowl_config,
)
from services.rainbowl_onboarding_service import (
    process_profile_channel_post,
)


class RainbowlProfileGate(commands.Cog):
    """rainbowl：プロフィールチャンネル投稿検知。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _get_config(
        self,
        guild_id: int,
    ) -> Optional[RainbowlGuildConfig]:
        try:
            return await get_rainbowl_config(guild_id)

        except RainbowlConfigNotFoundError:
            return None

        except RainbowlConfigError as exc:
            print(
                "[rainbowl] 設定取得エラー:"
                f" guild_id={guild_id} error={exc}"
            )
            return None

    @commands.Cog.listener()
    async def on_message(
        self,
        message: discord.Message,
    ) -> None:
        if message.guild is None:
            return

        if message.author.bot:
            return

        config = await self._get_config(message.guild.id)

        if config is None:
            return

        try:
            await process_profile_channel_post(
                message,
                config,
            )
        except Exception as exc:
            print(
                "[rainbowl] プロフィールチャンネル投稿検知に"
                "失敗しました:"
                f" guild_id={message.guild.id}"
                f" user_id={message.author.id}"
                f" error={exc}"
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RainbowlProfileGate(bot))
