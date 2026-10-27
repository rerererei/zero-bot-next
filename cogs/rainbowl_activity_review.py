# cogs/rainbowl_activity_review.py
"""
rainbowl：月次活動整理（在籍確認）の受け口。

- 「残留」ボタン（永続DynamicItem）の登録
- /kick_is_enabled：月ごとの集計・キックを実行するかどうかの設定
- /activity_review_preview：今月の実行時点の活動状況の確認（書き込み・通知は一切しない）
- /activity_review_test：指定した1人だけで月初の在籍確認を本番と同じ流れで試す（テスト用）
- /activity_exclude：個別除外の登録／解除／一覧（月次活動確認の判定から外す。休止中の人もこれで登録する）

集計・キック本体は cogs/daily_batch.py から
services/rainbowl_activity_review_service.py を呼び出して実行する。
"""

import io
from datetime import datetime
from typing import List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from services.rainbowl_config_service import (
    RainbowlConfigError,
    get_rainbowl_config,
    is_admin_like,
)
from services.rainbowl_activity_review_service import (
    JST,
    MONTH_PATTERN,
    RetainButton,
    build_preview_csv,
    deadline_label,
    default_kick_setting_month,
    format_preview_line,
    get_activity_review_config,
    get_exclusion_state,
    is_kick_enabled,
    list_exclusions,
    month_label,
    month_of,
    preview_current_month,
    register_exempt,
    set_kick_enabled,
    start_test_review,
    unregister_exempt,
)


class RainbowlActivityReview(commands.Cog):
    """rainbowl：月次活動整理（在籍確認）"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="kick_is_enabled",
        description="月次活動確認（集計・キック）をその月に実行するか設定します（運営専用）",
    )
    @app_commands.describe(
        enabled="True=通常どおり集計・キック / False=その月は集計もキックもしない（省略すると現在の設定を表示）",
        target_month="対象月（YYYY-MM、例: 2026-11）。省略すると今月（翌月1日の集計・8日のキックが対象）",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def kick_is_enabled(
        self,
        interaction: discord.Interaction,
        enabled: Optional[bool] = None,
        target_month: Optional[str] = None,
    ) -> None:
        config = await get_activity_review_config(interaction.guild_id)

        if config is None:
            await interaction.response.send_message(
                "このサーバーには月次活動確認（activity_review）が設定されていません。",
                ephemeral=True,
            )
            return

        if target_month is None:
            target_month = default_kick_setting_month(datetime.now(JST))
        elif not MONTH_PATTERN.match(target_month):
            await interaction.response.send_message(
                "対象月は YYYY-MM 形式で指定してください（例: 2026-11）。",
                ephemeral=True,
            )
            return

        label = f"{month_label(target_month)}分（翌月1日の集計・8日のキック）"

        if enabled is None:
            current = await is_kick_enabled(interaction.guild_id, target_month)
            await interaction.response.send_message(
                f"{label}：**{'有効' if current else '無効'}**",
                ephemeral=True,
            )
            return

        await set_kick_enabled(
            interaction.guild_id,
            target_month,
            enabled,
            interaction.user.id,
        )

        await interaction.response.send_message(
            f"{label}を **{'有効' if enabled else '無効'}** にしました。"
            + (
                ""
                if enabled
                else "\nこの月は集計を行わず、確認用ロール付与・通知・キックもしません。"
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="activity_review_preview",
        description="今月の活動状況（実行時点）を確認します（ロール付与・通知・キックはしません／運営専用）",
    )
    @app_commands.describe(
        output="表示形式（省略時はページ送りで表示）",
    )
    @app_commands.choices(
        output=[
            app_commands.Choice(name="ページ送りで表示", value="page"),
            app_commands.Choice(name="CSVファイル", value="csv"),
        ]
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def activity_review_preview(
        self,
        interaction: discord.Interaction,
        output: Optional[app_commands.Choice[str]] = None,
    ) -> None:
        config = await get_activity_review_config(interaction.guild_id)

        try:
            rb_config = await get_rainbowl_config(interaction.guild_id)
        except RainbowlConfigError:
            rb_config = None

        if config is None or rb_config is None:
            await interaction.response.send_message(
                "このサーバーには月次活動確認（activity_review）が設定されていません。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)

        try:
            summary, evaluations = await preview_current_month(
                interaction.guild,
                rb_config,
                config,
            )
        except Exception as exc:
            print(
                "[activity_review] プレビューに失敗しました:"
                f" guild_id={interaction.guild_id} error={exc}"
            )
            await interaction.followup.send(
                "プレビューの集計に失敗しました。",
            )
            return

        if output is not None and output.value == "csv":
            # Excelで文字化けしないようBOM付きUTF-8にする
            file = discord.File(
                io.BytesIO(
                    build_preview_csv(evaluations).encode("utf-8-sig")
                ),
                filename=(
                    "activity_review_preview_"
                    f"{datetime.now(JST):%Y%m%d_%H%M}.csv"
                ),
            )
            await interaction.followup.send(
                summary,
                file=file,
            )
            return

        pager = PreviewPager(
            [format_preview_line(ev) for ev in evaluations]
        )
        await interaction.followup.send(
            summary,
            embed=pager.build_embed(),
            view=pager if pager.max_page > 0 else discord.utils.MISSING,
        )

    @app_commands.command(
        name="activity_review_test",
        description="指定した1人だけで月初の在籍確認を本番と同じ流れで試します（管理者専用・テスト用）",
    )
    @app_commands.describe(
        target="テスト対象（キックされても困らないテスト用アカウント）",
        minutes="確認期限（何分後か。省略時は5分）",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def activity_review_test(
        self,
        interaction: discord.Interaction,
        target: discord.Member,
        minutes: app_commands.Range[int, 1, 1440] = 5,
    ) -> None:
        config = await get_activity_review_config(interaction.guild_id)

        try:
            rb_config = await get_rainbowl_config(interaction.guild_id)
        except RainbowlConfigError:
            rb_config = None

        if config is None or rb_config is None:
            await interaction.response.send_message(
                "このサーバーには月次活動確認（activity_review）が設定されていません。",
                ephemeral=True,
            )
            return

        # Botや管理者扱いの人はキックできない（本番でも対象外）ため弾く
        if target.bot or is_admin_like(target, rb_config):
            await interaction.response.send_message(
                "Bot・管理者・運営・Supporterはテスト対象にできません。"
                "テスト用アカウントを指定してください。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            deadline = await start_test_review(
                interaction.guild,
                rb_config,
                config,
                target,
                minutes,
            )
        except Exception as exc:
            print(
                "[activity_review] テスト実行に失敗しました:"
                f" guild_id={interaction.guild_id}"
                f" user_id={target.id} error={exc}"
            )
            await interaction.followup.send(
                f"テストの開始に失敗しました：{exc}",
                ephemeral=True,
            )
            return

        if deadline is None:
            await interaction.followup.send(
                f"{target.mention} は既に在籍確認が進行中のため、テストを開始できません。",
                ephemeral=True,
            )
            return

        kick_enabled = await is_kick_enabled(
            interaction.guild_id,
            month_of(deadline.date()),
        )

        await interaction.followup.send(
            f"{target.mention} で在籍確認のテストを開始しました。\n"
            f"・確認期限：{deadline_label(deadline)}\n"
            f"・確認用ロール付与／本人専用チャンネル作成／<#{config.staff_list_channel_id}> への一覧投稿を行いました\n"
            f"・期限を過ぎたら `/zbadmin run_daily_batch` で締め処理を実行できます"
            "（実行しなければ次の0:00に締めます）\n"
            + (
                "・【残留】を押さなければ、締め処理でキックされます"
                if kick_enabled
                else "・今月は /kick_is_enabled が無効のため、締め処理ではキックせず見送りになります"
            ),
            ephemeral=True,
        )

    # ========================================
    # /activity_exclude：個別除外
    # ========================================

    activity_exclude = app_commands.Group(
        name="activity_exclude",
        description="月次活動確認の個別除外（運営専用）",
        guild_only=True,
        default_permissions=discord.Permissions(administrator=True),
    )

    async def _check_exclude_target(
        self,
        interaction: discord.Interaction,
        target: Optional[discord.Member] = None,
    ) -> bool:
        """設定の有無と対象者を確認する。NGならメッセージを返してFalse。"""
        config = await get_activity_review_config(interaction.guild_id)

        if config is None:
            await interaction.response.send_message(
                "このサーバーには月次活動確認（activity_review）が設定されていません。",
                ephemeral=True,
            )
            return False

        if target is None:
            return True

        try:
            rb_config = await get_rainbowl_config(interaction.guild_id)
        except RainbowlConfigError:
            rb_config = None

        if target.bot or (rb_config and is_admin_like(target, rb_config)):
            await interaction.response.send_message(
                f"{target.mention} はBot・管理者扱いのため、もともと月次活動確認の対象外です。",
                ephemeral=True,
            )
            return False

        return True

    @staticmethod
    def _pending_review_note(state: dict) -> str:
        """在籍確認が進行中なら、登録だけではキックが止まらないことを添える。"""
        review = state.get("current_review") or {}

        if review.get("status") != "PENDING":
            return ""

        return (
            f"\n⚠️ {month_label(review['target_month'])}分の在籍確認が進行中です。"
            "今回の登録は進行中の確認には反映されないため、キックを止めるには【残留】を押してください。"
        )

    @activity_exclude.command(
        name="exempt",
        description="個別除外を登録します（休止中の人も含め、解除するまで月次活動確認の判定から外れます）",
    )
    @app_commands.describe(
        target="除外するメンバー",
        reason="理由（管理者向けの記録。休止なら「休止：〜12月まで」など期間も書いておく）",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def exclude_exempt(
        self,
        interaction: discord.Interaction,
        target: discord.Member,
        reason: app_commands.Range[str, 1, 200],
    ) -> None:
        if not await self._check_exclude_target(interaction, target):
            return

        await register_exempt(
            interaction.guild_id,
            target.id,
            reason,
            interaction.user.id,
        )
        state = await get_exclusion_state(interaction.guild_id, target.id)

        await interaction.response.send_message(
            f"{target.mention} を個別除外に登録しました。\n理由：{reason}"
            + self._pending_review_note(state),
            ephemeral=True,
        )

    @activity_exclude.command(
        name="unexempt",
        description="個別除外を解除します（翌月1日の集計から通常どおり判定されます）",
    )
    @app_commands.describe(target="個別除外を解除するメンバー")
    @app_commands.checks.has_permissions(administrator=True)
    async def exclude_unexempt(
        self,
        interaction: discord.Interaction,
        target: discord.User,
    ) -> None:
        if not await self._check_exclude_target(interaction):
            return

        state = await get_exclusion_state(interaction.guild_id, target.id)

        if not (state.get("exempt") or {}).get("is_exempt"):
            await interaction.response.send_message(
                f"{target.mention} は個別除外に登録されていません。",
                ephemeral=True,
            )
            return

        await unregister_exempt(interaction.guild_id, target.id)
        await interaction.response.send_message(
            f"{target.mention} の個別除外を解除しました。",
            ephemeral=True,
        )

    @activity_exclude.command(
        name="list",
        description="個別除外中のメンバーを一覧表示します",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def exclude_list(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if not await self._check_exclude_target(interaction):
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        items = await list_exclusions(interaction.guild_id)
        lines: List[str] = []

        for item in items:
            exempt = item.get("exempt") or {}
            lines.append(
                f"🛡 <@{item['user_id']}> {exempt.get('reason') or '（理由なし）'}"
                f"（{(exempt.get('granted_at') or '')[:10]} <@{exempt.get('granted_by')}>）"
            )

        if not lines:
            await interaction.followup.send(
                "個別除外中のメンバーはいません。",
                ephemeral=True,
            )
            return

        description = "\n".join(lines)
        if len(description) > 4000:
            description = description[:4000] + "\n…（以下省略）"

        await interaction.followup.send(
            embed=discord.Embed(
                title=f"個別除外（{len(lines)}件）",
                description=description,
                color=discord.Color.blurple(),
            ),
            ephemeral=True,
        )


class PreviewPager(discord.ui.View):
    """/activity_review_preview のページ送り表示（チャンネルへ通常投稿）。"""

    PER_PAGE = 15

    def __init__(self, lines: List[str]):
        super().__init__(timeout=600)
        self.lines = lines
        self.page = 0
        self.max_page = max(0, (len(lines) - 1) // self.PER_PAGE)
        self._update_buttons()

    def build_embed(self) -> discord.Embed:
        start = self.page * self.PER_PAGE
        chunk = self.lines[start:start + self.PER_PAGE]
        embed = discord.Embed(
            title="メンバー別の活動状況（基準未達→スコアの低い順）",
            description="\n".join(chunk) or "判定対象のメンバーがいません。",
            color=discord.Color.gold(),
        )
        embed.set_footer(
            text=f"{self.page + 1} / {self.max_page + 1} ページ（{len(self.lines)}名）"
        )
        return embed

    def _update_buttons(self) -> None:
        self.prev_page.disabled = self.page <= 0
        self.next_page.disabled = self.page >= self.max_page

    async def _show(self, interaction: discord.Interaction) -> None:
        self._update_buttons()
        await interaction.response.edit_message(
            embed=self.build_embed(),
            view=self,
        )

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.page = max(0, self.page - 1)
        await self._show(interaction)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.page = min(self.max_page, self.page + 1)
        await self._show(interaction)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RainbowlActivityReview(bot))

    # 永続ボタン：custom_idにユーザーID・対象月を含むため、DynamicItemで登録する
    bot.add_dynamic_items(RetainButton)
