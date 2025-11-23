# cogs/zbadmin_commands.py

import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional

from data.store import (
    get_voice_xp,
    get_text_xp,
    calc_level_from_xp,
    get_voice_meta,
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

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ★ クラスの「中」でグループ定義すること！
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

        # ★ 重め処理になるので先に ACK を返しておく
        await interaction.response.defer(ephemeral=False)

        guild = interaction.guild
        target = user or interaction.user  # 指定なければ自分
        guild_id = guild.id
        user_id = target.id

        # ===== メタ情報取得（分単位） =====
        meta = get_voice_meta(guild_id, user_id)

        total_min = float(meta.get("total_time", 0.0))
        solo_min = float(meta.get("solo_time", 0.0))
        small_min = float(meta.get("small_group_time", 0.0))
        mid_min = float(meta.get("mid_group_time", 0.0))
        big_min = float(meta.get("big_group_time", 0.0))
        muted_min = float(meta.get("muted_time", 0.0))
        max_count = int(meta.get("max_member_count", 0))

        # 時間帯バケット（0〜23時、単位: 分）
        hour_buckets = meta.get("hour_buckets", [0.0] * 24)
        if not isinstance(hour_buckets, list) or len(hour_buckets) != 24:
            hour_buckets = [0.0] * 24

        # 0〜6, 6〜12, 12〜18, 18〜24 ごとに合計（分）
        min_0_6   = sum(hour_buckets[0:6])
        min_6_12  = sum(hour_buckets[6:12])
        min_12_18 = sum(hour_buckets[12:18])
        min_18_24 = sum(hour_buckets[18:24])

        # ペア滞在時間（相手ごとの minutes）
        pair_time = meta.get("pair_time", {})
        if not isinstance(pair_time, dict):
            pair_time = {}

        # { "user_id(str)": minutes } → 滞在時間の多い順にソート
        sorted_pairs = sorted(
            pair_time.items(),
            key=lambda x: float(x[1]),
            reverse=True,
        )

        # ===== Embed 整形 =====
        embed = discord.Embed(
            title=f"ボイス統計：{target.display_name}",
            description="VC滞在時間の統計情報 📊",
            color=discord.Color.blue(),
        )

        # 総滞在時間（表示だけ秒に変換してフォーマット）
        embed.add_field(
            name="📈 総滞在時間",
            value=_fmt_duration(total_min * 60),
            inline=False,
        )

        # 人数帯ごとの時間（割合は分ベースでOK）
        embed.add_field(
            name="👤 一人の時間",
            value=f"{_fmt_duration(solo_min * 60)}（{_pct(solo_min, total_min)}）",
            inline=True,
        )
        embed.add_field(
            name="👥 2〜3人",
            value=f"{_fmt_duration(small_min * 60)}（{_pct(small_min, total_min)}）",
            inline=True,
        )
        embed.add_field(
            name="\N{BUSTS IN SILHOUETTE} 4〜6人",
            value=f"{_fmt_duration(mid_min * 60)}（{_pct(mid_min, total_min)}）",
            inline=True,
        )
        embed.add_field(
            name="🎉 7人以上",
            value=f"{_fmt_duration(big_min * 60)}（{_pct(big_min, total_min)}）",
            inline=True,
        )
        embed.add_field(
            name="🔇 ミュート状態の時間",
            value=_fmt_duration(muted_min * 60),
            inline=True,
        )
        embed.add_field(
            name="👪 一緒にいた最大人数",
            value=f"{max_count} 人",
            inline=True,
        )

        # 時間帯別（分）
        embed.add_field(
            name="⏰ 時間帯別滞在時間（合計）",
            value=(
                f"0〜 6時 : {int(min_0_6)}分\n"
                f"6〜12時 : {int(min_6_12)}分\n"
                f"12〜18時: {int(min_12_18)}分\n"
                f"18〜24時: {int(min_18_24)}分"
            ),
            inline=False,
        )

        # 一緒にいた人（全員）＋上位3人メダル表示
        pair_time = meta.get("pair_time", {})
        if not isinstance(pair_time, dict):
            pair_time = {}

        # { "user_id(str)": minutes } → 滞在時間の多い順にソート
        sorted_pairs = sorted(
            pair_time.items(),
            key=lambda x: float(x[1]),
            reverse=True,
        )

        if sorted_pairs:
            lines = []
            for idx, (uid_str, mins) in enumerate(sorted_pairs):
                # ユーザーIDを int に変換
                try:
                    pid = int(uid_str)
                except ValueError:
                    partner = None
                    name = f"(ID: {uid_str})"
                else:
                    # ① まずキャッシュから取得
                    partner = guild.get_member(pid)

                    # ② キャッシュにいなければ REST で取りにいく
                    if partner is None:
                        try:
                            partner = await guild.fetch_member(pid)
                        except discord.NotFound:
                            partner = None

                    if partner is None:
                        # それでもダメなら最後のフォールバック
                        name = f"(ID: {pid})"
                    else:
                        # ★ 表示名（ニックネーム優先）
                        name = partner.display_name

                time_text = _fmt_duration(float(mins) * 60)

                # ★ 上位3人だけメダル、それ以外は「・」
                if idx == 0:
                    prefix = "🥇"
                elif idx == 1:
                    prefix = "🥈"
                elif idx == 2:
                    prefix = "🥉"
                else:
                    prefix = "・"

                lines.append(f"{prefix} {name} — {time_text}")

            text = "\n".join(lines)
            if len(text) > 1000:
                text = text[:1000] + "\n…（一部省略）"

            embed.add_field(
                name="👥 一緒にいた人",
                value=text,
                inline=False,
            )

        # ★ defer 済みなので followup で返す
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(ZBAdmin(bot))
