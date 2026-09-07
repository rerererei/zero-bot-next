# cogs/rainbowl_role_buttons.py
"""
rainbowl専用：セルフサービスのロール付与ボタン。

ロール一覧・見出し文・説明文は data/rainbowl/role_buttons.json から読み込む。
コードを変更せずJSON編集だけでロールの追加・削除・文言変更ができる。

運営が /set_role_button を実行すると、対象チャンネル内のBotメッセージを
全削除したうえで、JSONの内容に基づいてメッセージを再投稿する
（カテゴリーごとに1メッセージ）。
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from services.rainbowl_config_service import (
    RainbowlConfigError,
    RainbowlConfigNotFoundError,
    get_rainbowl_config,
)
from utils.interaction_cooldown import is_on_cooldown


ROLE_BUTTONS_JSON_PATH = Path("data/rainbowl/role_buttons.json")

ROLE_TOGGLE_CUSTOM_ID_PREFIX = "rainbowl_role_toggle:"

TOGGLE_COOLDOWN_SECONDS = 1.0

MAX_ROWS_PER_VIEW = 5


def load_role_buttons_data() -> List[Dict[str, Any]]:
    """role_buttons.jsonを読み込み、categoriesのリストを返す。"""
    with ROLE_BUTTONS_JSON_PATH.open(encoding="utf-8") as f:
        data = json.load(f)

    categories = data.get("categories")

    if not isinstance(categories, list):
        raise ValueError(
            "role_buttons.jsonの形式が不正です"
            "（categoriesがリストではありません）"
        )

    return categories


def _parse_role_id_from_custom_id(
    interaction: discord.Interaction,
) -> Optional[int]:
    custom_id = (
        interaction.data.get("custom_id", "")
        if interaction.data
        else ""
    )

    if not custom_id.startswith(ROLE_TOGGLE_CUSTOM_ID_PREFIX):
        return None

    role_id_text = custom_id[len(ROLE_TOGGLE_CUSTOM_ID_PREFIX):]

    try:
        return int(role_id_text)
    except ValueError:
        return None


BUTTON_STYLE_BY_NAME = {
    "primary": discord.ButtonStyle.primary,
    "secondary": discord.ButtonStyle.secondary,
    "success": discord.ButtonStyle.success,
    "danger": discord.ButtonStyle.danger,
}

DEFAULT_BUTTON_STYLE_NAME = "success"


class RoleToggleButton(discord.ui.Button):
    """押すたびにロールの付与/剥奪を切り替えるボタン。"""

    def __init__(
        self,
        label: str,
        role_id: int,
        row: int,
        style_name: str = DEFAULT_BUTTON_STYLE_NAME,
    ):
        style = BUTTON_STYLE_BY_NAME.get(
            style_name,
            BUTTON_STYLE_BY_NAME[DEFAULT_BUTTON_STYLE_NAME],
        )

        super().__init__(
            label=label,
            style=style,
            custom_id=f"{ROLE_TOGGLE_CUSTOM_ID_PREFIX}{role_id}",
            row=row,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        cog = interaction.client.get_cog("RainbowlRoleButtons")

        if cog is None:
            await interaction.response.send_message(
                "現在このボタンは利用できません。",
                ephemeral=True,
            )
            return

        await cog.handle_role_toggle_button(interaction)


class RoleButtonCategoryView(discord.ui.View):
    """1カテゴリー分のロール付与ボタンをまとめた永続View。"""

    def __init__(self, category: Dict[str, Any]):
        super().__init__(timeout=None)

        groups = category.get("groups") or []

        for row, group in enumerate(groups):
            if row >= MAX_ROWS_PER_VIEW:
                break

            for role_entry in group.get("roles") or []:
                self.add_item(
                    RoleToggleButton(
                        label=role_entry["label"],
                        role_id=int(role_entry["role_id"]),
                        row=row,
                        style_name=role_entry.get(
                            "style",
                            DEFAULT_BUTTON_STYLE_NAME,
                        ),
                    )
                )


def _build_category_embed(
    category: Dict[str, Any],
) -> discord.Embed:
    return discord.Embed(
        title=category.get("title", ""),
        description=category.get("description", ""),
        color=discord.Color.green(),
    )


class RainbowlRoleButtons(commands.Cog):
    """rainbowl：セルフサービスのロール付与ボタン。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _get_config(self, guild_id: int):
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

    # ========================================
    # ロール付与/剥奪ボタン
    # ========================================

    async def handle_role_toggle_button(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if (
            interaction.guild is None
            or not isinstance(interaction.user, discord.Member)
        ):
            await interaction.response.send_message(
                "サーバー内で使用してください。",
                ephemeral=True,
            )
            return

        role_id = _parse_role_id_from_custom_id(interaction)

        if role_id is None:
            await interaction.response.send_message(
                "ボタンの情報を読み取れませんでした。",
                ephemeral=True,
            )
            return

        if is_on_cooldown(
            (interaction.user.id, role_id),
            TOGGLE_COOLDOWN_SECONDS,
        ):
            await interaction.response.send_message(
                "少し間隔をあけてもう一度押してください。",
                ephemeral=True,
            )
            return

        member = interaction.user
        role = interaction.guild.get_role(role_id)

        if role is None:
            await interaction.response.send_message(
                "対象のロールが見つかりませんでした。"
                "運営へお問い合わせください。",
                ephemeral=True,
            )
            return

        try:
            if role in member.roles:
                await member.remove_roles(
                    role,
                    reason="rainbowl: ロールボタンで剥奪",
                )
                await interaction.response.send_message(
                    f"「{role.name}」ロールを外しました。",
                    ephemeral=True,
                )
            else:
                await member.add_roles(
                    role,
                    reason="rainbowl: ロールボタンで付与",
                )
                await interaction.response.send_message(
                    f"「{role.name}」ロールを付与しました。",
                    ephemeral=True,
                )
        except discord.HTTPException as exc:
            print(
                "[rainbowl] ロールボタンの処理に失敗しました"
                f" guild_id={interaction.guild.id}"
                f" user_id={member.id} role_id={role_id}"
                f" error={exc}"
            )
            await interaction.response.send_message(
                "ロールの更新に失敗しました。"
                "運営へお問い合わせください。",
                ephemeral=True,
            )

    # ========================================
    # 運営向け：ボタン再設置コマンド
    # ========================================

    @app_commands.command(
        name="set_role_button",
        description=(
            "ロール付与ボタンを再設置します"
            "（既存メッセージは削除されます・運営専用）"
        ),
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def set_role_button(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        config = await self._get_config(interaction.guild_id)

        if config is None:
            await interaction.followup.send(
                "rainbowl設定を取得できませんでした。",
                ephemeral=True,
            )
            return

        try:
            categories = load_role_buttons_data()
        except Exception as exc:
            print(
                "[rainbowl] role_buttons.jsonの読み込みに"
                f"失敗しました: error={exc}"
            )
            await interaction.followup.send(
                "role_buttons.jsonの読み込みに失敗しました。",
                ephemeral=True,
            )
            return

        channel = interaction.guild.get_channel(
            config.role_button_channel_id
        )

        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send(
                "ロール付与ボタン用チャンネルが"
                "見つかりませんでした。",
                ephemeral=True,
            )
            return

        try:
            await channel.purge(
                limit=50,
                check=lambda m: (
                    m.author.id == interaction.client.user.id
                ),
            )
        except discord.HTTPException as exc:
            print(
                "[rainbowl] 既存メッセージの削除に失敗しました"
                f" channel_id={channel.id} error={exc}"
            )

        for category in categories:
            await channel.send(
                embed=_build_category_embed(category),
                view=RoleButtonCategoryView(category),
            )

        await interaction.followup.send(
            f"ロール付与ボタンを再設置しました → {channel.mention}",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RainbowlRoleButtons(bot))

    # 永続View：Bot起動のたびに再登録する
    try:
        categories = load_role_buttons_data()
    except Exception as exc:
        print(
            "[rainbowl] role_buttons.jsonの読み込みに失敗しました"
            f"（永続View未登録）: error={exc}"
        )
        return

    for category in categories:
        bot.add_view(RoleButtonCategoryView(category))
