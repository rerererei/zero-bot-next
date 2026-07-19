import asyncio
from dataclasses import dataclass
from typing import Dict, List, Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from config import (
    BDSM_COMMAND_LOG_CHANNEL_ID,
    BDSM_FEMALE_PROFILE_CHANNEL_ID,
    BDSM_FEMALE_URL_CHANNEL_ID,
    BDSM_MALE_PROFILE_CHANNEL_ID,
    BDSM_MALE_URL_CHANNEL_ID,
)
from services.bdsm_channel_service import (
    BdsmResultEntry,
    collect_latest_profile_urls,
    collect_latest_results,
    find_latest_user_result,
)
from services.bdsm_service import (
    BdsmMatchError,
    BdsmResultNotFoundError,
    REQUEST_TIMEOUT_SECONDS,
    fetch_match_score,
)


API_REQUEST_INTERVAL_SECONDS = 0.2
RESULTS_PER_EMBED = 20


@dataclass(frozen=True)
class BdsmRankingEntry:
    """相性ランキング1件分の情報。"""

    user_id: int
    display_name: str
    score: int
    profile_url: Optional[str]


class BdsmCommands(commands.Cog):
    """BDSM相性診断コマンド。"""

    def __init__(
        self,
        bot: commands.Bot,
    ) -> None:
        self.bot = bot

    @app_commands.command(
        name="bdsm_check",
        description=(
            "このチャンネルに登録されている"
            "ユーザーとのBDSM相性を確認します"
        ),
    )
    @app_commands.guild_only()
    async def bdsm_check(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """
        実行チャンネルに投稿されている全ユーザーとの
        BDSM相性を計算し、実行者だけに表示する。
        """
        valid_channel_ids = {
            BDSM_MALE_URL_CHANNEL_ID,
            BDSM_FEMALE_URL_CHANNEL_ID,
        }

        if interaction.channel_id not in valid_channel_ids:
            await interaction.response.send_message(
                (
                    "このコマンドは、男性用または女性用の"
                    "BDSM結果URLチャンネルでのみ使用できます。"
                ),
                ephemeral=True,
            )
            return

        # 処理中表示も実行者にしか見せない
        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        # 実行履歴を指定チャンネルへ送信
        await self._send_command_log(interaction)

        male_url_channel = await self._get_text_channel(
            BDSM_MALE_URL_CHANNEL_ID
        )
        female_url_channel = await self._get_text_channel(
            BDSM_FEMALE_URL_CHANNEL_ID
        )

        if (
            male_url_channel is None
            or female_url_channel is None
        ):
            await interaction.edit_original_response(
                content=(
                    "BDSM結果URLチャンネルを取得できませんでした。"
                    "\nBotの閲覧権限と設定値を確認してください。"
                )
            )
            return

        target_channel = (
            male_url_channel
            if interaction.channel_id
            == BDSM_MALE_URL_CHANNEL_ID
            else female_url_channel
        )

        # 実行者の診断結果は男女両チャンネルから検索する
        own_result = await find_latest_user_result(
            channels=[
                male_url_channel,
                female_url_channel,
            ],
            user_id=interaction.user.id,
        )

        if own_result is None:
            await interaction.edit_original_response(
                content=(
                    "あなたのBDSM診断結果URLが"
                    "見つかりませんでした。\n"
                    "男性用または女性用チャンネルに、"
                    "次の形式でURLを投稿してください。\n"
                    "`https://bdsmtest.org/r/xxxxxxxx`"
                )
            )
            return

        # コマンドを実行したチャンネルの全登録者を取得
        target_results = await collect_latest_results(
            channel=target_channel,
            exclude_user_id=interaction.user.id,
        )

        if not target_results:
            await interaction.edit_original_response(
                content=(
                    "比較対象となる診断結果が"
                    "このチャンネルにありません。"
                )
            )
            return

        profile_urls = await self._get_profile_urls(
            target_channel_id=target_channel.id,
            target_results=target_results,
        )

        ranking_results: List[BdsmRankingEntry] = []
        failed_users: List[str] = []

        timeout = aiohttp.ClientTimeout(
            total=REQUEST_TIMEOUT_SECONDS,
        )

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:
            for index, target in enumerate(
                target_results
            ):
                try:
                    score = await fetch_match_score(
                        result_id=own_result.result_id,
                        partner_id=target.result_id,
                        session=session,
                    )

                    display_name = (
                        self._get_current_display_name(
                            interaction=interaction,
                            entry=target,
                        )
                    )

                    ranking_results.append(
                        BdsmRankingEntry(
                            user_id=target.user_id,
                            display_name=display_name,
                            score=score,
                            profile_url=profile_urls.get(
                                target.user_id
                            ),
                        )
                    )

                except (
                    BdsmResultNotFoundError,
                    BdsmMatchError,
                    ValueError,
                ):
                    failed_users.append(
                        target.display_name
                    )

                # APIへ一気にリクエストを送らないための待機
                if index < len(target_results) - 1:
                    await asyncio.sleep(
                        API_REQUEST_INTERVAL_SECONDS
                    )

        if not ranking_results:
            await interaction.edit_original_response(
                content=(
                    "相性診断結果を取得できませんでした。\n"
                    "投稿されているURLが有効か確認してください。"
                )
            )
            return

        # 相性スコア降順
        ranking_results.sort(
            key=lambda item: (
                -item.score,
                item.display_name.casefold(),
            )
        )

        embeds = self._create_ranking_embeds(
            interaction=interaction,
            target_channel=target_channel,
            ranking_results=ranking_results,
            failed_count=len(failed_users),
        )

        # 最初のランキングをEphemeralの元レスポンスへ表示
        await interaction.edit_original_response(
            content=None,
            embed=embeds[0],
        )

        # 件数が多い場合も、追加結果は実行者だけに表示
        for embed in embeds[1:]:
            await interaction.followup.send(
                embed=embed,
                ephemeral=True,
            )

    async def _send_command_log(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """コマンド実行履歴を指定チャンネルへ送信する。"""
        log_channel = await self._get_text_channel(
            BDSM_COMMAND_LOG_CHANNEL_ID
        )

        if log_channel is None:
            print(
                "[BDSM] コマンド実行ログチャンネルを"
                "取得できません。"
                f" channel_id={BDSM_COMMAND_LOG_CHANNEL_ID}"
            )
            return

        channel_label = self._get_url_channel_label(
            interaction.channel_id
        )

        embed = discord.Embed(
            title="🔍 BDSM相性診断 実行ログ",
            color=discord.Color.purple(),
            timestamp=discord.utils.utcnow(),
        )

        embed.add_field(
            name="実行者",
            value=(
                f"{interaction.user.mention}\n"
                f"`{interaction.user.id}`"
            ),
            inline=True,
        )

        embed.add_field(
            name="実行チャンネル",
            value=(
                f"{channel_label}\n"
                f"<#{interaction.channel_id}>"
            ),
            inline=True,
        )

        try:
            await log_channel.send(embed=embed)
        except discord.HTTPException as exc:
            print(
                "[BDSM] コマンド実行ログの送信に"
                f"失敗しました: {exc}"
            )

    async def _get_profile_urls(
        self,
        target_channel_id: int,
        target_results: List[BdsmResultEntry],
    ) -> Dict[int, str]:
        """比較対象ユーザーのプロフィールURLを取得する。"""
        profile_channel_id = (
            BDSM_MALE_PROFILE_CHANNEL_ID
            if target_channel_id
            == BDSM_MALE_URL_CHANNEL_ID
            else BDSM_FEMALE_PROFILE_CHANNEL_ID
        )

        # プロフィールチャンネル未設定
        if profile_channel_id == 0:
            return {}

        profile_channel = await self._get_text_channel(
            profile_channel_id
        )

        if profile_channel is None:
            print(
                "[BDSM] プロフィールチャンネルを"
                "取得できません。"
                f" channel_id={profile_channel_id}"
            )
            return {}

        user_ids = {
            entry.user_id
            for entry in target_results
        }

        return await collect_latest_profile_urls(
            channel=profile_channel,
            user_ids=user_ids,
        )

    async def _get_text_channel(
        self,
        channel_id: int,
    ) -> Optional[discord.TextChannel]:
        """チャンネルをキャッシュまたはAPIから取得する。"""
        channel = self.bot.get_channel(channel_id)

        if isinstance(channel, discord.TextChannel):
            return channel

        try:
            fetched_channel = await self.bot.fetch_channel(
                channel_id
            )
        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
        ):
            return None

        if isinstance(
            fetched_channel,
            discord.TextChannel,
        ):
            return fetched_channel

        return None

    def _get_current_display_name(
        self,
        interaction: discord.Interaction,
        entry: BdsmResultEntry,
    ) -> str:
        """現在のサーバーニックネームを取得する。"""
        if interaction.guild is None:
            return entry.display_name

        member = interaction.guild.get_member(
            entry.user_id
        )

        if member is None:
            return entry.display_name

        return member.display_name

    def _get_url_channel_label(
        self,
        channel_id: Optional[int],
    ) -> str:
        """BDSM URLチャンネルの種別名を返す。"""
        if channel_id == BDSM_MALE_URL_CHANNEL_ID:
            return "男性用BDSM URLチャンネル"

        if channel_id == BDSM_FEMALE_URL_CHANNEL_ID:
            return "女性用BDSM URLチャンネル"

        return "不明なチャンネル"

    def _create_ranking_embeds(
        self,
        interaction: discord.Interaction,
        target_channel: discord.TextChannel,
        ranking_results: List[BdsmRankingEntry],
        failed_count: int,
    ) -> List[discord.Embed]:
        """相性ランキング表示用Embedを生成する。"""
        chunks = [
            ranking_results[
                index:index + RESULTS_PER_EMBED
            ]
            for index in range(
                0,
                len(ranking_results),
                RESULTS_PER_EMBED,
            )
        ]

        embeds: List[discord.Embed] = []

        for page_index, chunk in enumerate(
            chunks,
            start=1,
        ):
            lines: List[str] = []

            for result in chunk:
                display_name = (
                    discord.utils.escape_markdown(
                        result.display_name
                    )
                )

                if result.profile_url:
                    profile_text = (
                        f"[プロフ]({result.profile_url})"
                    )
                else:
                    profile_text = "プロフなし"

                lines.append(
                    f"**{result.score}%**　"
                    f"{display_name}　"
                    f"{profile_text}"
                )

            title = "💞 BDSM相性ランキング"

            if len(chunks) > 1:
                title += (
                    f"（{page_index}/{len(chunks)}）"
                )

            embed = discord.Embed(
                title=title,
                description="\n".join(lines),
                color=discord.Color.magenta(),
                timestamp=discord.utils.utcnow(),
            )

            embed.add_field(
                name="比較対象",
                value=target_channel.mention,
                inline=True,
            )

            embed.add_field(
                name="実行者",
                value=interaction.user.mention,
                inline=True,
            )

            footer_text = (
                f"取得成功: {len(ranking_results)}人"
            )

            if failed_count:
                footer_text += (
                    f" / 取得失敗: {failed_count}人"
                )

            embed.set_footer(text=footer_text)

            embeds.append(embed)

        return embeds


async def setup(
    bot: commands.Bot,
) -> None:
    await bot.add_cog(BdsmCommands(bot))
