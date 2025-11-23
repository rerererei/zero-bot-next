import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional

from data.store import (
    get_voice_xp,
    get_text_xp,
    calc_level_from_xp,
    get_voice_meta,   # ★ 追加
)


def _fmt_duration(sec: float) -> str:
    """秒 → 『○時間△分▢秒』みたいな日本語表記にする"""
    sec = int(sec)
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60

    if h > 0:
        if m > 0:
            return f"{h}時間{m}分"
        return f"{h}時間"
    if m > 0:
        if s > 0:
            return f"{m}分{s}秒"
        return f"{m}分"
    return f"{s}秒"


def _pct(part: float, whole: float) -> str:
    """割合（%）を文字列化"""
    if whole <= 0:
        return "0.0%"
    return f"{part / whole * 100:.1f}%"


class ZBAdmin(commands.Cog):
    """管理者専用コマンドグループ"""

    def __init__(self, bot):
        self.bot = bot

    # グループ定義
    zbadmin = app_commands.Group(
        name="zbadmin",
        description="ZERO BOT 管理者専用コマンド",
        default_permissions=discord.Permissions(administrator=True),
    )

    # ------------------------
    # /zbadmin show_xp
    # ------------------------
    @zbadmin.command(
        name="show_xp",
        description="指定ユーザーのXPを表示（管理者専用）"
    )
    @app_commands.describe(user="XPを確認する対象ユーザー")
    async def show_xp(self, interaction: discord.Interaction, user: discord.Member):

        # 二重ガード（MissingPermissionsを出させない）
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "このコマンドは **管理者専用** だよ。",
                ephemeral=True
            )
            return

        guild_id = interaction.guild.id

        voice_xp = get_voice_xp(guild_id, user.id)
        text_xp = get_text_xp(guild_id, user.id)

        v_lv, v_cur, v_need = calc_level_from_xp(voice_xp)
        t_lv, t_cur, t_need = calc_level_from_xp(text_xp)

        embed = discord.Embed(
            title=f"XP情報：{user.display_name}",
            description="管理者ビュー",
            color=0xFF5555
        )
        embed.add_field(
            name="🎤 ボイス",
            value=(
                f"Lv.{v_lv} / {voice_xp:.1f} XP\n"
                f"（次Lvまで {v_cur:.1f} / {v_need:.1f}）"
            ),
            inline=False
        )
        embed.add_field(
            name="💬 テキスト",
            value=(
                f"Lv.{t_lv} / {text_xp:.1f} XP\n"
                f"（次Lvまで {t_cur:.1f} / {t_need:.1f}）"
            ),
            inline=False
        )

        await interaction.response.send_message(embed=embed)

    # ------------------------
    # /zbadmin voice_stats
    # ------------------------
    @zbadmin.command(
        name="voice_stats",
        description="指定ユーザーのボイス通話統計を表示（管理者専用）",
    )
    @app_commands.describe(
        user="統計を確認する対象ユーザー（省略時は自分）",
    )
    async def voice_stats(
        self,
        interaction: discord.Interaction,
        user: Optional[discord.Member] = None,
    ):

        # 二重ガード
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "このコマンドは **管理者専用** だよ。",
                ephemeral=True
            )
            return

        if interaction.guild is None:
            await interaction.response.send_message(
                "サーバー内で実行してね。",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        target = user or interaction.user  # 指定なければ自分
        guild_id = guild.id
        user_id = target.id

        # ===== メタ情報取得 =====
        meta = get_voice_meta(guild_id, user_id)

        total = float(meta.get("total_time", 0))
        solo = float(meta.get("solo_time", 0))
        small = float(meta.get("small_group_time", 0))
        mid = float(meta.get("mid_group_time", 0))
        big = float(meta.get("big_group_time", 0))
        muted = float(meta.get("muted_time", 0))
        max_count = int(meta.get("max_member_count", 0))

        # ===== Embed 整形 =====
        embed = discord.Embed(
            title=f"ボイス統計：{target.display_name}",
            description="VC滞在時間のざっくり統計だよ 📊",
            color=discord.Color.blue(),
        )

        embed.add_field(
            name="📈 総滞在時間",
            value=_fmt_duration(total),
            inline=False,
        )

        embed.add_field(
            name="👤 一人の時間",
            value=f"{_fmt_duration(solo)}（{_pct(solo, total)}）",
            inline=True,
        )
        embed.add_field(
            name="👥 2〜3人",
            value=f"{_fmt_duration(small)}（{_pct(small, total)}）",
            inline=True,
        )
        embed.add_field(
            name="\N{BUSTS IN SILHOUETTE} 4〜6人",
            value=f"{_fmt_duration(mid)}（{_pct(mid, total)}）",
            inline=True,
        )
        embed.add_field(
            name="🎉 7人以上",
            value=f"{_fmt_duration(big)}（{_pct(big, total)}）",
            inline=True,
        )
        embed.add_field(
            name="🔇 ミュート状態の時間",
            value=_fmt_duration(muted),
            inline=True,
        )
        embed.add_field(
            name="👪 一緒にいた最大人数",
            value=f"{max_count} 人",
            inline=True,
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=False,  # 管理用なのでとりあえず非公開
        )


async def setup(bot):
    await bot.add_cog(ZBAdmin(bot))
