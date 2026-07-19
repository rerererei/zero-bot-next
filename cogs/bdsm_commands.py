import asyncio
from dataclasses import dataclass
from typing import Dict, List, Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from services.bdsm_channel_service import (
    BdsmResultEntry,
    collect_latest_profile_urls,
    collect_latest_results,
    find_latest_user_result,
)
from services.bdsm_config_service import (
    BdsmConfigError,
    BdsmConfigNotFoundError,
    BdsmGuildConfig,
    get_bdsm_config,
)
from services.bdsm_service import (
    BdsmMatchError,
    BdsmRateLimitError,
    BdsmResultNotFoundError,
    REQUEST_TIMEOUT_SECONDS,
    fetch_match_score,
)


API_REQUEST_INTERVAL_SECONDS = 0.2
RESULTS_PER_EMBED = 20


@dataclass(frozen=True)
class BdsmRankingEntry:
    """ランキング表示用の1件分の情報。"""

    user_id: int
    display_name: str
    score: int
    profile_url: Optional[str]


class BdsmCommands(commands.Cog):
    """BDSM相性診断機能。"""

    def __init__(
        self,
        bot: commands.Bot,
    ) -> None:
        self.bot = bot

    @app_commands.command(
        name="bdsm_check",
        description=(
            "このチャンネルに登録された"
            "ユーザーとのBDSM相性を確認します"
        ),
    )
    @app_commands.guild_only()
    async def bdsm_check(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """登録ユーザーとの相性ランキングを表示する。"""
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "このコマンドはサーバー内でのみ使用できます。",
                ephemeral=True,
            )
            return

        # DynamoDB取得や履歴検索に時間がかかる可能性があるため、
        # 最初にEphemeralで応答を確保する
        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        try:
            config = await get_bdsm_config(
                interaction.guild_id
            )

        except BdsmConfigNotFoundError:
            await interaction.edit_original_response(
                content=(
                    "このサーバーではBDSM相性診断が"
                    "設定されていません。"
                )
            )
            return

        except BdsmConfigError as exc:
            print(
                "[BDSM] 設定取得エラー:"
                f" guild_id={interaction.guild_id}"
                f" error={exc}"
            )

            await interaction.edit_original_response(
                content=(
                    "BDSM相性診断の設定を"
                    "取得できませんでした。"
                )
            )
            return

        if not config.enabled:
            await interaction.edit_original_response(
                content=(
                    "このサーバーではBDSM相性診断が"
                    "現在無効になっています。"
                )
            )
            return

        valid_channel_ids = {
            config.male_url_channel_id,
            config.female_url_channel_id,
        }

        if (
            interaction.channel_id
            not in valid_channel_ids
        ):
            await interaction.edit_original_response(
                content=(
                    "このコマンドは、設定されている"
                    "男性用または女性用の"
                    "BDSM結果URLチャンネルで"
                    "実行してください。"
                )
            )
            return

        # 有効な実行だけログへ記録
        await self._send_command_log(
            interaction=interaction,
            config=config,
        )

        male_url_channel = (
            await self._get_text_channel(
                config.male_url_channel_id
            )
        )

        female_url_channel = (
            await self._get_text_channel(
                config.female_url_channel_id
            )
        )

        if (
            male_url_channel is None
            or female_url_channel is None
        ):
            await interaction.edit_original_response(
                content=(
                    "BDSM結果URLチャンネルを"
                    "取得できませんでした。\n"
                    "Botの閲覧権限とDB設定を"
                    "確認してください。"
                )
            )
            return

        if (
            interaction.channel_id
            == config.male_url_channel_id
        ):
            target_channel = male_url_channel
        else:
            target_channel = female_url_channel

        # 実行者自身の診断結果は、
        # 男女両方のURLチャンネルから探す
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
                    "見つかりませんでした。\n\n"
                    "男性用または女性用チャンネルに、"
                    "次の形式でURLを投稿してください。\n"
                    "`https://bdsmtest.org/r/xxxxxxxx`"
                )
            )
            return

        # コマンドを実行したチャンネルに登録されている
        # 全ユーザーの最新診断結果を取得
        target_results = await collect_latest_results(
            channel=target_channel,
            exclude_user_id=interaction.user.id,
        )

        if not target_results:
            await interaction.edit_original_response(
                content=(
                    "このチャンネルには、"
                    "比較対象となる診断結果がありません。"
                )
            )
            return

        # 既存DB設定に登録されたプロフィールチャンネルを取得
        profile_channels = (
            await self._get_profile_channels(
                config
            )
        )

        profile_urls: Dict[int, str] = {}

        if profile_channels:
            target_user_ids = {
                entry.user_id
                for entry in target_results
            }

            profile_urls = (
                await collect_latest_profile_urls(
                    channels=profile_channels,
                    user_ids=target_user_ids,
                )
            )

        ranking_results: List[
            BdsmRankingEntry
        ] = []

        failed_users: List[str] = []

        timeout = aiohttp.ClientTimeout(
            total=REQUEST_TIMEOUT_SECONDS,
        )

        rate_limited = False

        # APIは同時実行せず、1件ずつ順番に処理する
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

                    ranking_results.append(
                        BdsmRankingEntry(
                            user_id=target.user_id,
                            display_name=(
                                self._get_display_name(
                                    interaction=interaction,
                                    entry=target,
                                )
                            ),
                            score=score,
                            profile_url=(
                                profile_urls.get(
                                    target.user_id
                                )
                            ),
                        )
                    )

                except BdsmRateLimitError as exc:
                    print(
                        "[BDSM] レート制限:"
                        f" guild_id={interaction.guild_id}"
                        f" error={exc}"
                    )

                    rate_limited = True
                    break

                except (
                    BdsmResultNotFoundError,
                    BdsmMatchError,
                    ValueError,
                ) as exc:
                    print(
                        "[BDSM] 相性取得失敗:"
                        f" guild_id={interaction.guild_id}"
                        f" user_id={target.user_id}"
                        f" display_name={target.display_name}"
                        f" error={exc}"
                    )

                    failed_users.append(
                        target.display_name
                    )

                # 相手サイトへ連続で負荷を掛けすぎないよう待機
                if index < len(target_results) - 1:
                    await asyncio.sleep(
                        API_REQUEST_INTERVAL_SECONDS
                    )

        if not ranking_results:
            message = (
                "相性診断結果を取得できませんでした。\n"
                "投稿されているURLが有効か"
                "確認してください。"
            )

            if rate_limited:
                message += (
                    "\n\n現在、相性診断サイト側の"
                    "利用回数制限が発生しています。"
                )

            await interaction.edit_original_response(
                content=message
            )
            return

        # 相性パーセンテージの高い順
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
            rate_limited=rate_limited,
        )

        # 元のEphemeralレスポンスへ1ページ目を表示
        await interaction.edit_original_response(
            content=None,
            embed=embeds[0],
        )

        # 20人を超える場合もEphemeralで追加表示
        for embed in embeds[1:]:
            await interaction.followup.send(
                embed=embed,
                ephemeral=True,
            )

    async def _send_command_log(
        self,
        interaction: discord.Interaction,
        config: BdsmGuildConfig,
    ) -> None:
        """指定されたログチャンネルへ実行履歴を送信する。"""
        log_channel = await self._get_text_channel(
            config.command_log_channel_id
        )

        if log_channel is None:
            print(
                "[BDSM] 実行ログチャンネルを"
                "取得できません。"
                f" guild_id={config.guild_id}"
                f" channel_id="
                f"{config.command_log_channel_id}"
            )
            return

        channel_label = (
            self._get_url_channel_label(
                channel_id=interaction.channel_id,
                config=config,
            )
        )

        executed_at = discord.utils.utcnow()
        unix_timestamp = int(
            executed_at.timestamp()
        )

        embed = discord.Embed(
            title="🔍 BDSM相性診断 実行ログ",
            color=discord.Color.purple(),
            timestamp=executed_at,
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

        embed.add_field(
            name="実行日時",
            value=f"<t:{unix_timestamp}:F>",
            inline=False,
        )

        try:
            await log_channel.send(
                embed=embed
            )

        except discord.HTTPException as exc:
            print(
                "[BDSM] 実行ログの送信に"
                f"失敗しました: {exc}"
            )

    async def _get_profile_channels(
        self,
        config: BdsmGuildConfig,
    ) -> List[discord.TextChannel]:
        """
        DBのprofile.profile_source_channel_idsに
        設定されているプロフィールチャンネルを取得する。
        """
        channels: List[
            discord.TextChannel
        ] = []

        for channel_id in (
            config.profile_source_channel_ids
        ):
            channel = await self._get_text_channel(
                channel_id
            )

            if channel is None:
                print(
                    "[BDSM] プロフィールチャンネルを"
                    "取得できません。"
                    f" guild_id={config.guild_id}"
                    f" channel_id={channel_id}"
                )
                continue

            channels.append(channel)

        return channels

    async def _get_text_channel(
        self,
        channel_id: int,
    ) -> Optional[discord.TextChannel]:
        """
        キャッシュまたはDiscord APIから、
        テキストチャンネルを取得する。
        """
        cached_channel = self.bot.get_channel(
            channel_id
        )

        if isinstance(
            cached_channel,
            discord.TextChannel,
        ):
            return cached_channel

        try:
            fetched_channel = (
                await self.bot.fetch_channel(
                    channel_id
                )
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

    def _get_display_name(
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
        config: BdsmGuildConfig,
    ) -> str:
        """実行されたURLチャンネルの種別を返す。"""
        if (
            channel_id
            == config.male_url_channel_id
        ):
            return "男性用BDSM URLチャンネル"

        if (
            channel_id
            == config.female_url_channel_id
        ):
            return "女性用BDSM URLチャンネル"

        return "不明なチャンネル"

    def _create_ranking_embeds(
        self,
        interaction: discord.Interaction,
        target_channel: discord.TextChannel,
        ranking_results: List[
            BdsmRankingEntry
        ],
        failed_count: int,
        rate_limited: bool,
    ) -> List[discord.Embed]:
        """相性ランキング表示用のEmbedを生成する。"""
        chunks = [
            ranking_results[
                index:
                index + RESULTS_PER_EMBED
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
                        f"[プロフ]"
                        f"({result.profile_url})"
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
                    f"（{page_index}/"
                    f"{len(chunks)}）"
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

            footer_parts = [
                (
                    f"取得成功: "
                    f"{len(ranking_results)}人"
                )
            ]

            if failed_count:
                footer_parts.append(
                    f"取得失敗: {failed_count}人"
                )

            if rate_limited:
                footer_parts.append(
                    "途中で利用制限が発生"
                )

            embed.set_footer(
                text=" / ".join(
                    footer_parts
                )
            )

            embeds.append(embed)

        return embeds


async def setup(
    bot: commands.Bot,
) -> None:
    await bot.add_cog(
        BdsmCommands(bot)
    )
