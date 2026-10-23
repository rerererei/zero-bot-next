# cogs/rainbowl_activity_review.py
"""
rainbowl：月次活動整理（在籍確認）の受け口。

- 「残留」ボタン（永続DynamicItem）の登録
- /kick_is_enabled：月ごとの集計・キックを実行するかどうかの設定
- /activity_review_preview：今月の実行時点の活動状況の確認（書き込み・通知は一切しない）

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
)
from services.rainbowl_activity_review_service import (
    JST,
    MONTH_PATTERN,
    RetainButton,
    build_preview_csv,
    default_kick_setting_month,
    format_preview_line,
    get_activity_review_config,
    is_kick_enabled,
    month_label,
    preview_current_month,
    set_kick_enabled,
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
