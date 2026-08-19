import asyncio
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from data.guild_config_store import GuildConfigStore
from utils.rankcard_draw import build_rank_card_file

logger = logging.getLogger(__name__)

guild_config_store = GuildConfigStore()

RANKCARD_OPEN_BUTTON_CUSTOM_ID = "rankcard:open_button"

# ギルドごとに1個：連打・同時押下を1件ずつに直列化するためのロック
_rankcard_locks: dict[int, asyncio.Lock] = {}


def _get_rankcard_lock(guild_id: int) -> asyncio.Lock:
    lock = _rankcard_locks.get(guild_id)
    if lock is None:
        lock = asyncio.Lock()
        _rankcard_locks[guild_id] = lock
    return lock


def get_rankcard_button_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    """常設RANK CARDボタンの設置先チャンネル（rankcard.button_channel_id）を取得する。"""
    config = guild_config_store.get_config(guild.id) or {}
    rankcard_cfg = config.get("rankcard") or {}
    raw_id = rankcard_cfg.get("button_channel_id")

    if not raw_id:
        return None

    try:
        chan_id = int(raw_id)
    except (TypeError, ValueError):
        return None

    channel = guild.get_channel(chan_id)
    return channel if isinstance(channel, discord.TextChannel) else None


class RankCardOpenButton(discord.ui.Button):
    """常設チャンネルに設置する「RANK CARD」ボタン。"""

    def __init__(self) -> None:
        super().__init__(
            label="レベル確認",
            style=discord.ButtonStyle.primary,
            emoji="🪪",
            custom_id=RANKCARD_OPEN_BUTTON_CUSTOM_ID,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("ZBCommands")

        if cog is None:
            await interaction.response.send_message(
                "現在この機能は利用できません。", ephemeral=True
            )
            return

        await cog.handle_rankcard_button(interaction)


class RankCardOpenButtonView(discord.ui.View):
    """「RANK CARD」ボタンの永続View。"""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(RankCardOpenButton())


class ZBCommands(commands.Cog):
    """ZERO BOT の管理用コマンドをまとめたCog"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def handle_rankcard_button(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "サーバー内で実行してください。", ephemeral=True
            )
            return

        lock = _get_rankcard_lock(guild.id)

        if lock.locked():
            await interaction.response.send_message(
                "他の人がRANK CARDを生成中です。少し待ってからもう一度押してね。",
                ephemeral=True,
            )
            return

        async with lock:
            await interaction.response.defer()

            original_message = interaction.message
            button_channel = (
                original_message.channel
                if original_message is not None
                else interaction.channel
            )

            # ★ interactionのfollowupは使わない（元メッセージへの「返信」表示になってしまうため）
            #   channelへ直接新規メッセージとして投稿する
            file = await build_rank_card_file(self.bot, guild, interaction.user)

            if isinstance(button_channel, discord.TextChannel):
                await button_channel.send(file=file)

            if original_message is not None:
                try:
                    await original_message.delete()
                except discord.HTTPException:
                    pass

            if isinstance(button_channel, discord.TextChannel):
                await button_channel.send(view=RankCardOpenButtonView())

    @app_commands.command(
        name="set_rankcard",
        description="常設のRANK CARDボタンを設定チャンネルへ設置します（管理者専用）",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def set_rankcard(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        channel = get_rankcard_button_channel(guild)

        if channel is None:
            await interaction.followup.send(
                "❌ 設置先チャンネルが見つかりません。\n"
                "`rankcard.button_channel_id` の設定を確認してください。",
                ephemeral=True,
            )
            return

        await channel.send(view=RankCardOpenButtonView())
        await interaction.followup.send(
            f"✅ {channel.mention} にボタンを設置しました。", ephemeral=True
        )


async def setup(bot: commands.Bot):
    logger.info("[ZB] loading zb cog...")
    await bot.add_cog(ZBCommands(bot))
    bot.add_view(RankCardOpenButtonView())
    logger.info("[ZB] zb cog loaded.")
