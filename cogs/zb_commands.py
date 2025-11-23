import discord
from discord import app_commands
from discord.ext import commands
from utils.rankcard_draw import generate_rank_card 

from data.store import (
    get_voice_xp,
    get_text_xp,
    calc_level_from_xp,
)


class ZBCommands(commands.Cog):
    """ZERO BOT NEXT の /zb コマンドをまとめたCog"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # メイングループ /zb
    zb = app_commands.Group(
        name="zb",
        description="ZERO BOT NEXT のメインコマンド"
    )

    # 管理者向けサブグループ /zb show ...
    show = app_commands.Group(
        name="show",
        description="管理者専用の表示コマンド",
        parent=zb,
        # ← ここから default_member_permissions を削除
    )

    # -----------------------
    # /zb ping
    # -----------------------
    @zb.command(name="ping", description="応答テスト")
    async def ping(self, interaction: discord.Interaction):
        await interaction.response.send_message("pong! from ZERO BOT NEXT")

    # -----------------------
    # /zb voicexp（自分のみ）
    # -----------------------
    @zb.command(
        name="voicexp",
        description="あなたのボイスXPとレベルを表示します"
    )
    async def voicexp(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message(
                "サーバー内で実行してね。",
                ephemeral=True
            )
            return

        guild_id = interaction.guild.id
        user_id = interaction.user.id
        xp = get_voice_xp(guild_id, user_id)

        level, cur, need = calc_level_from_xp(xp)

        await interaction.response.send_message(
            f"🎤 あなたのボイスレベル: **Lv.{level}**\n"
            f"XP: **{xp:.1f} XP**（次のLvまで {cur:.1f} / {need:.1f}）"
        )

    # -----------------------
    # /zb textxp（自分のみ）
    # -----------------------
    @zb.command(
        name="textxp",
        description="あなたのテキストXPとレベルを表示します"
    )
    async def textxp(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message(
                "サーバー内で実行してね。",
                ephemeral=True
            )
            return

        guild_id = interaction.guild.id
        user_id = interaction.user.id
        xp = get_text_xp(guild_id, user_id)

        level, cur, need = calc_level_from_xp(xp)

        await interaction.response.send_message(
            f"💬 あなたのテキストレベル: **Lv.{level}**\n"
            f"XP: **{xp:.1f} XP**（次のLvまで {cur:.1f} / {need:.1f}）"
        )

    # -----------------------
    # /zb rank
    # -----------------------
    @zb.command(name="rank")
    async def rank(self, interaction: discord.Interaction):

        # ★ zb_rank.py に完全委譲（XP計算だけメインでやる選択肢もある）
        await generate_rank_card(self.bot, interaction)

async def setup(bot: commands.Bot):
    await bot.add_cog(ZBCommands(bot))
