# cogs/zbadmin_commands.py

import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional
import logging

from data.xp_router import (
    get_voice_xp,
    get_text_xp,
    add_voice_xp,        # ★ これを必ず入れる
    add_text_xp,         # ★ これも必ず入れる
    calc_level_from_xp,
    get_voice_meta,
    get_guild_user_stats,
)

from utils.helpers import _xp_for_level
from utils.rankcard_draw import generate_rank_card
import datetime

from data.voice_daily_store import (
    get_guild_total_minutes_in_range,
    get_user_total_minutes_in_range,
)

logger = logging.getLogger(__name__)

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

class RankPaginator(discord.ui.View):
    def __init__(
        self,
        entries: list[tuple[discord.Member, float, int]],
        *,
        per_page: int = 10,
        title: str = "ランキング",
        kind: str = "voice",
        author_id: Optional[int] = None,
        guild_name: str = "",
        timeout: float = 180.0,
    ):
        """
        entries: [(member, xp, level), ...] のリスト
        kind: "voice" or "text"（埋め込みタイトルとかに使う）
        """
        super().__init__(timeout=timeout)
        self.entries = entries
        self.per_page = per_page
        self.title = title
        self.kind = kind
        self.author_id = author_id
        self.guild_name = guild_name
        self.current_page = 0  # 0-based

    # ページ数
    @property
    def max_page(self) -> int:
        if not self.entries:
            return 1
        return (len(self.entries) - 1) // self.per_page + 1

    def make_embed(self) -> discord.Embed:
        start = self.current_page * self.per_page
        end = start + self.per_page
        page_entries = self.entries[start:end]

        lines: list[str] = []
        for idx, (member, xp, level) in enumerate(page_entries, start=start + 1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(idx, f"{idx}位")
            lines.append(
                f"{medal} **{member.display_name}** — "
                f"Lv.{level} / `{xp:.1f}` XP"
            )

        if not lines:
            desc = "データがありません。"
        else:
            desc = "\n".join(lines)

        embed = discord.Embed(
            title=self.title,
            description=desc,
            color=discord.Color.gold()
            if self.kind == "voice"
            else discord.Color.blurple(),
        )
        footer = f"サーバー: {self.guild_name} | ページ {self.current_page + 1}/{self.max_page}"
        embed.set_footer(text=footer)
        return embed

    async def _ensure_author(self, interaction: discord.Interaction) -> bool:
        """コマンド実行者以外が押したときは無視 or エラーメッセージ"""
        if self.author_id is None:
            return True
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "このランキングの操作はコマンド実行者のみが行えます。",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="⏮ 戻る", style=discord.ButtonStyle.secondary)
    async def prev_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not await self._ensure_author(interaction):
            return

        if self.current_page > 0:
            self.current_page -= 1
        else:
            # 先頭からさらに戻ろうとしたら末尾にループ
            self.current_page = self.max_page - 1

        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    @discord.ui.button(label="次へ ⏭", style=discord.ButtonStyle.secondary)
    async def next_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not await self._ensure_author(interaction):
            return

        if self.current_page < self.max_page - 1:
            self.current_page += 1
        else:
            # 最後から次に行こうとしたら先頭に戻す
            self.current_page = 0

        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    @discord.ui.button(label="✖ 閉じる", style=discord.ButtonStyle.danger)
    async def close(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not await self._ensure_author(interaction):
            return

        # ボタンを全部無効化して更新
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

        await interaction.response.edit_message(view=self)
        self.stop()

    async def on_timeout(self):
        # タイムアウトしたらボタンを無効化
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        # メッセージ本体は取得できないので、呼び出し側が放置でOK

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

        # 管理者チェック（ここは軽いので defer 前でOK）
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "このコマンドは **管理者専用** だよ。",
                ephemeral=True
            )
            return

        # 🔹 先に defer してインタラクションを延命
        await interaction.response.defer(ephemeral=True)

        guild_id = interaction.guild.id

        voice_xp = get_voice_xp(guild_id, user.id)
        text_xp = get_text_xp(guild_id, user.id)

        v_lv, v_cur, v_need = calc_level_from_xp(guild_id, voice_xp)
        t_lv, t_cur, t_need = calc_level_from_xp(guild_id, text_xp)

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

        # 🔹 defer 済みなので followup で返す
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------
    # /zbadmin rank
    # ------------------------
    @zbadmin.command(
        name="rank",
        description="指定ユーザーのRANK CARDを表示（管理者専用）",
    )
    @app_commands.describe(user="RANK CARDを表示する対象ユーザー")
    async def rank(self, interaction: discord.Interaction, user: discord.Member):

        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "このコマンドは **管理者専用** だよ。",
                ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True)
        await generate_rank_card(self.bot, interaction, target_user=user)

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

    # ------------------------
    # /zbadmin setxp（加算方式）
    # ------------------------
    @zbadmin.command(
        name="setxp",
        description="指定ユーザーのXPを加算します（管理者専用）",
    )
    @app_commands.describe(
        user="XPを変更する対象ユーザー",
        target="ボイスかテキストか",
        xp="加算するXP量（マイナス指定も可能）",
    )
    @app_commands.choices(
        target=[
            app_commands.Choice(name="ボイスXP", value="voice"),
            app_commands.Choice(name="テキストXP", value="text"),
        ]
    )
    async def setxp(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        target: app_commands.Choice[str],
        xp: float,
    ):
        # 管理者チェック
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "このコマンドは **管理者専用** だよ。",
                ephemeral=True,
            )
            return

        if interaction.guild is None:
            await interaction.response.send_message(
                "サーバー内で実行してね。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        guild_id = interaction.guild.id

        # ★ 現在XPに「加算」する処理
        if target.value == "voice":
            add_voice_xp(guild_id, user.id, xp)
            new_xp = get_voice_xp(guild_id, user.id)
        else:
            add_text_xp(guild_id, user.id, xp)
            new_xp = get_text_xp(guild_id, user.id)

        # 新しいXPからレベル計算
        lv, cur, need = calc_level_from_xp(guild_id, new_xp)

        xp_kind = "ボイス" if target.value == "voice" else "テキスト"

        await interaction.followup.send(
            (
                f"✅ `{user.display_name}` の **{xp_kind} XP** に `{xp}` XP 加算しました。\n"
                f"→ 現在XP: **{new_xp:.1f} XP**\n"
                f"→ Lv.{lv}（次Lvまで {cur:.1f} / {need:.1f}）"
            ),
            ephemeral=True,
        )

    # ------------------------
    # /zbadmin setlv
    # ------------------------
    @zbadmin.command(
        name="setlv",
        description="指定ユーザーを指定レベルになるようにXPを調整します（管理者専用）",
    )
    @app_commands.describe(
        user="レベルを変更する対象ユーザー",
        target="ボイスかテキストか",
        level="設定したいレベル（そのレベルになるXPを自動計算）",
    )
    @app_commands.choices(
        target=[
            app_commands.Choice(name="ボイスXP", value="voice"),
            app_commands.Choice(name="テキストXP", value="text"),
        ]
    )
    async def setlv(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        target: app_commands.Choice[str],
        level: int,
    ):
        # 管理者チェック（二重ガード）
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "このコマンドは **管理者専用** だよ。",
                ephemeral=True,
            )
            return

        if interaction.guild is None:
            await interaction.response.send_message(
                "サーバー内で実行してね。",
                ephemeral=True,
            )
            return

        if level < 1:
            await interaction.response.send_message(
                "レベルは 1 以上を指定してね。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        guild_id = interaction.guild.id

        # そのレベルになるために必要な通算XPを逆算
        target_xp = _xp_for_level(level, guild_id)

        if target.value == "voice":
            current_xp = get_voice_xp(guild_id, user.id)
            delta = target_xp - current_xp
            add_voice_xp(guild_id, user.id, delta)
        else:
            current_xp = get_text_xp(guild_id, user.id)
            delta = target_xp - current_xp
            add_text_xp(guild_id, user.id, delta)

        # 念のため結果を再計算して表示
        v_lv, v_cur, v_need = calc_level_from_xp(guild_id, target_xp)

        xp_kind = "ボイス" if target.value == "voice" else "テキスト"

        await interaction.followup.send(
            (
                f"✅ `{user.display_name}` を **{xp_kind} Lv.{level}** 相当のXPに設定しました。\n"
                f"→ 通算XP: **{target_xp:.1f} XP**（内部計算結果: Lv.{v_lv}, 次レベルまで {v_cur:.1f} / {v_need:.1f}）"
            ),
            ephemeral=True,
        )

    # ------------------------
    # /zbadmin voicerank
    # ------------------------
    @zbadmin.command(
        name="voicerank",
        description="サーバー内のボイスXPランキング（ページング対応）を表示します（管理者専用）",
    )
    async def voicerank(
        self,
        interaction: discord.Interaction,
    ):
        # 管理者ガード
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "このコマンドは **管理者専用** だよ。",
                ephemeral=True,
            )
            return

        if interaction.guild is None:
            await interaction.response.send_message(
                "サーバー内で実行してね。",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        guild_id = guild.id

        await interaction.response.defer(ephemeral=False)

        stats = get_guild_user_stats(guild_id) or {}
        entries: list[tuple[discord.Member, float, int]] = []

        for uid_raw, data in stats.items():
            try:
                uid = int(uid_raw)
            except (TypeError, ValueError):
                continue

            member = guild.get_member(uid)
            if member is None:
                continue

            voice_xp = float(data.get("voice_xp", 0.0))
            if voice_xp <= 0:
                continue

            level, _, _ = calc_level_from_xp(guild_id, voice_xp)
            entries.append((member, voice_xp, level))

        # XP降順でソート
        entries.sort(key=lambda x: x[1], reverse=True)

        if not entries:
            await interaction.followup.send("まだボイスXPが記録されているメンバーがいないみたい…。")
            return

        view = RankPaginator(
            entries=entries,
            per_page=10,
            title="🎤 ボイスXPランキング",
            kind="voice",
            author_id=interaction.user.id,
            guild_name=guild.name,
        )

        await interaction.followup.send(
            embed=view.make_embed(),
            view=view,
        )

    # ------------------------
    # /zbadmin textrank
    # ------------------------
    @zbadmin.command(
        name="textrank",
        description="サーバー内のテキストXPランキング（ページング対応）を表示します（管理者専用）",
    )
    async def textrank(
        self,
        interaction: discord.Interaction,
    ):
        # 管理者ガード
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "このコマンドは **管理者専用** だよ。",
                ephemeral=True,
            )
            return

        if interaction.guild is None:
            await interaction.response.send_message(
                "サーバー内で実行してね。",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        guild_id = guild.id

        await interaction.response.defer(ephemeral=False)

        stats = get_guild_user_stats(guild_id) or {}
        entries: list[tuple[discord.Member, float, int]] = []

        for uid_raw, data in stats.items():
            try:
                uid = int(uid_raw)
            except (TypeError, ValueError):
                continue

            member = guild.get_member(uid)
            if member is None:
                continue

            text_xp = float(data.get("text_xp", 0.0))
            if text_xp <= 0:
                continue

            level, _, _ = calc_level_from_xp(guild_id, text_xp)
            entries.append((member, text_xp, level))

        entries.sort(key=lambda x: x[1], reverse=True)

        if not entries:
            await interaction.followup.send("まだテキストXPが記録されているメンバーがいないみたい…。")
            return

        view = RankPaginator(
            entries=entries,
            per_page=10,
            title="💬 テキストXPランキング",
            kind="text",
            author_id=interaction.user.id,
            guild_name=guild.name,
        )

        await interaction.followup.send(
            embed=view.make_embed(),
            view=view,
        )

    # ------------------------
    # /zbadmin voicerank_period
    # ------------------------
    @zbadmin.command(
        name="voicerank_period",
        description="指定期間のボイス通話時間ランキング（サーバー全体）を表示します",
    )
    @app_commands.describe(
        date_from="集計開始日 (YYYYMMDD)",
        date_to="集計終了日 (YYYYMMDD)",
        top_n="表示する件数（1〜50）",
    )
    async def voicerank_period(
        self,
        interaction: discord.Interaction,
        date_from: str,
        date_to: str,
        top_n: int = 10,
    ):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "このコマンドは **管理者専用** だよ。",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "サーバー内で実行してね。",
                ephemeral=True,
            )
            return

        # 入力: YYYYMMDD
        try:
            start = datetime.datetime.strptime(date_from, "%Y%m%d").date()
            end = datetime.datetime.strptime(date_to, "%Y%m%d").date()
        except ValueError:
            await interaction.response.send_message(
                "日付の形式は `YYYYMMDD` で指定してね。\n例: `20251101`",
                ephemeral=True,
            )
            return

        if start > end:
            await interaction.response.send_message(
                "開始日が終了日より後になってるよ。",
                ephemeral=True,
            )
            return

        # 表示用: YYYY/MM/DD
        start_str = start.strftime("%Y/%m/%d")
        end_str   = end.strftime("%Y/%m/%d")

        top_n = max(1, min(top_n, 50))
        await interaction.response.defer(ephemeral=False)

        totals = get_guild_total_minutes_in_range(
            guild_id=guild.id,
            date_from=start,
            date_to=end,
        )

        if not totals:
            await interaction.followup.send(
                f"{start_str} 〜 {end_str} の間に VC データがなかったよ。",
            )
            return

        sorted_items = sorted(totals.items(), key=lambda x: x[1], reverse=True)

        lines = []
        for idx, (uid, minutes) in enumerate(sorted_items, start=1):
            member = guild.get_member(uid)
            name = member.display_name if member else f"(ID: {uid})"
            time_text = _fmt_duration(minutes * 60)
            lines.append(f"`{idx:>2}` {name} — {time_text}")

        lines = lines[:top_n]

        title = f"🎤 VC時間ランキング（{start_str} 〜 {end_str}）"
        PER_PAGE = 10

        if len(lines) <= PER_PAGE:
            embed = discord.Embed(
                title=title,
                description="\n".join(lines),
                color=discord.Color.gold(),
            )
            await interaction.followup.send(embed=embed)
            return

        view = PeriodRankPaginator(lines=lines, per_page=PER_PAGE)
        view.page = 0
        embed = discord.Embed(
            title=title,
            description="\n".join(lines[:PER_PAGE]),
            color=discord.Color.gold(),
        )
        embed.set_footer(text=f"Page 1/{(len(lines)-1)//PER_PAGE + 1}")
        await interaction.followup.send(embed=embed, view=view)

    # ------------------------
    # /zbadmin voice_time_period
    # ------------------------
    @zbadmin.command(
        name="voice_time_period",
        description="指定ユーザーの指定期間のボイス滞在時間を表示（管理者専用）",
    )
    @app_commands.describe(
        user="対象ユーザー（省略時は自分）",
        date_from="集計開始日 (YYYYMMDD)",
        date_to="集計終了日 (YYYYMMDD)",
    )
    async def voice_time_period(
        self,
        interaction: discord.Interaction,
        user: Optional[discord.Member],
        date_from: str,
        date_to: str,
    ):
        # 管理者権限チェック
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "このコマンドは **管理者専用** だよ。",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "サーバー内で実行してね。",
                ephemeral=True,
            )
            return

        target = user or interaction.user

        # 日付入力 YYYYMMDD
        try:
            start = datetime.datetime.strptime(date_from, "%Y%m%d").date()
            end = datetime.datetime.strptime(date_to, "%Y%m%d").date()
        except ValueError:
            await interaction.response.send_message(
                "日付の形式は `YYYYMMDD` で指定してね。\n例: `20251101`",
                ephemeral=True,
            )
            return

        if start > end:
            await interaction.response.send_message(
                "開始日が終了日より後になってるよ。",
                ephemeral=True,
            )
            return

        # 表示用
        start_str = start.strftime("%Y/%m/%d")
        end_str   = end.strftime("%Y/%m/%d")

        await interaction.response.defer(ephemeral=False)

        # 集計
        total_min = get_user_total_minutes_in_range(
            guild_id=guild.id,
            user_id=target.id,
            date_from=start,
            date_to=end,
        )

        time_text = _fmt_duration(total_min * 60)

        # 🎤 Embed 作成（アイコン付き）
        embed = discord.Embed(
            title=f"🎤 期間VC時間：{target.display_name}",
            description=(
                f"期間: **{start_str} 〜 {end_str}**\n"
                f"合計VC時間: **{time_text}**"
            ),
            color=discord.Color.blue(),
        )

        # ⭐ アイコン表示（thumbnail）
        if target.avatar:
            embed.set_thumbnail(url=target.avatar.url)
        else:
            embed.set_thumbnail(url=target.default_avatar.url)

        await interaction.followup.send(embed=embed)


class PeriodRankPaginator(discord.ui.View):
    """期間ランキング用のシンプルなページャ"""

    def __init__(self, lines: list[str], per_page: int = 10):
        super().__init__(timeout=60)
        self.lines = lines
        self.per_page = per_page
        self.page = 0

    def _max_page(self) -> int:
        if not self.lines:
            return 0
        return (len(self.lines) - 1) // self.per_page

    def _make_embed(self, title: str) -> discord.Embed:
        start = self.page * self.per_page
        end = start + self.per_page
        chunk = self.lines[start:end]

        desc = "\n".join(chunk) if chunk else "データがありません。"

        embed = discord.Embed(
            title=title,
            description=desc,
            color=discord.Color.gold(),
        )
        embed.set_footer(text=f"Page {self.page + 1}/{self._max_page() + 1}")
        return embed

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if self.page > 0:
            self.page -= 1
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if self.page < self._max_page():
            self.page += 1
        await interaction.response.edit_message(view=self)

async def setup(bot: commands.Bot):
    logger.info("[ZBADMIN] loading zbadmin cog...")
    await bot.add_cog(ZBAdmin(bot))
    logger.info("[ZBADMIN] zbadmin cog loaded.")
