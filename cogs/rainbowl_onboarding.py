# cogs/rainbowl_onboarding.py
"""
rainbowl機能：on_member_join、「次へ」ボタン、「入会申請」ボタン。

判定・DynamoDB操作・チャンネル操作は
services/rainbowl_onboarding_service.py に委譲する。
"""

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from services.rainbowl_config_service import (
    RainbowlConfigError,
    RainbowlConfigNotFoundError,
    RainbowlGuildConfig,
    get_rainbowl_config,
)
from services.rainbowl_onboarding_service import (
    build_join_log_embed,
    fetch_user_bio,
    process_apply_button,
    process_member_join,
    process_next_button,
)


# 現在の設計では、入会案内カテゴリーは7チャンネル固定
# （末尾の「入会申請」を除く1〜6チャンネルに「次へ」ボタンを設置する）。
# チャンネル数を変更した場合はここも合わせて変更すること。
ONBOARDING_NEXT_BUTTON_STEPS = range(1, 7)

NEXT_BUTTON_CUSTOM_ID_PREFIX = "rainbowl_onboarding_next:"
APPLY_BUTTON_CUSTOM_ID = "rainbowl_apply"


def _parse_next_button_step(
    interaction: discord.Interaction,
) -> Optional[int]:
    """
    ボタンのcustom_idから対象stepを読み取る。

    Viewインスタンスのメンバ変数は再起動後は当てにならないため、
    必ずcustom_id側から判定する。
    """
    custom_id = (
        interaction.data.get("custom_id", "")
        if interaction.data
        else ""
    )

    if not custom_id.startswith(
        NEXT_BUTTON_CUSTOM_ID_PREFIX
    ):
        return None

    step_text = custom_id[
        len(NEXT_BUTTON_CUSTOM_ID_PREFIX):
    ]

    try:
        return int(step_text)
    except ValueError:
        return None


class NextOnboardingButton(discord.ui.Button):
    """入会案内チャンネルに設置する「次へ」ボタン。"""

    def __init__(self, step: int):
        super().__init__(
            label="次へ",
            style=discord.ButtonStyle.primary,
            custom_id=(
                f"{NEXT_BUTTON_CUSTOM_ID_PREFIX}{step}"
            ),
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        cog = interaction.client.get_cog(
            "RainbowlOnboarding"
        )

        if cog is None:
            await interaction.response.send_message(
                "現在このボタンは利用できません。",
                ephemeral=True,
            )
            return

        await cog.handle_next_button(interaction)


class RainbowlNextButtonView(discord.ui.View):
    """「次へ」ボタンの永続View。"""

    def __init__(self, step: int):
        super().__init__(timeout=None)
        self.add_item(NextOnboardingButton(step))


class ApplyButton(discord.ui.Button):
    """「入会申請」チャンネルに設置するボタン。"""

    def __init__(self):
        super().__init__(
            label="入会申請",
            style=discord.ButtonStyle.success,
            custom_id=APPLY_BUTTON_CUSTOM_ID,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        cog = interaction.client.get_cog(
            "RainbowlOnboarding"
        )

        if cog is None:
            await interaction.response.send_message(
                "現在このボタンは利用できません。",
                ephemeral=True,
            )
            return

        await cog.handle_apply_button(interaction)


class RainbowlApplyButtonView(discord.ui.View):
    """「入会申請」ボタンの永続View。"""

    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(ApplyButton())


class RainbowlOnboarding(commands.Cog):
    """rainbowl：入場〜入会申請までの処理。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _get_config(
        self,
        guild_id: int,
    ) -> Optional[RainbowlGuildConfig]:
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
    # 入場
    # ========================================

    @commands.Cog.listener()
    async def on_member_join(
        self,
        member: discord.Member,
    ) -> None:
        if member.bot:
            return

        config = await self._get_config(member.guild.id)

        if config is None:
            return

        try:
            item = await process_member_join(
                member,
                config,
            )
        except Exception as exc:
            print(
                "[rainbowl] on_member_join処理に"
                "失敗しました:"
                f" guild_id={member.guild.id}"
                f" user_id={member.id} error={exc}"
            )
            return

        bio = await fetch_user_bio(self.bot, member.id)
        embed = build_join_log_embed(member, item, bio)

        join_log_channel = member.guild.get_channel(
            config.join_log_channel_id
        )

        if join_log_channel is None:
            print(
                "[rainbowl] 入場者詳細チャンネルを"
                "取得できません"
                f" guild_id={member.guild.id}"
            )
            return

        try:
            await join_log_channel.send(embed=embed)
        except discord.HTTPException as exc:
            print(
                "[rainbowl] 入場ログの投稿に"
                f"失敗しました: error={exc}"
            )

    # ========================================
    # 「次へ」ボタン
    # ========================================

    async def handle_next_button(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if (
            interaction.guild is None
            or not isinstance(
                interaction.user,
                discord.Member,
            )
        ):
            await interaction.response.send_message(
                "サーバー内で使用してください。",
                ephemeral=True,
            )
            return

        step = _parse_next_button_step(interaction)

        if step is None:
            await interaction.response.send_message(
                "ボタンの情報を読み取れませんでした。",
                ephemeral=True,
            )
            return

        config = await self._get_config(
            interaction.guild_id
        )

        if config is None:
            await interaction.response.send_message(
                "設定を取得できませんでした。",
                ephemeral=True,
            )
            return

        next_step = await process_next_button(
            interaction.user,
            config,
            step,
        )

        if next_step is None:
            await interaction.response.send_message(
                "このボタンは現在使用できません"
                "（既に進んでいるか、対象外です）。",
                ephemeral=True,
            )
            return

        next_channel_id = config.onboarding_channel_ids[
            next_step - 1
        ]

        await interaction.response.send_message(
            "次のチャンネルを開放しました → "
            f"<#{next_channel_id}>",
            ephemeral=True,
        )

    # ========================================
    # 「入会申請」ボタン
    # ========================================

    async def handle_apply_button(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if (
            interaction.guild is None
            or not isinstance(
                interaction.user,
                discord.Member,
            )
        ):
            await interaction.response.send_message(
                "サーバー内で使用してください。",
                ephemeral=True,
            )
            return

        config = await self._get_config(
            interaction.guild_id
        )

        if config is None:
            await interaction.response.send_message(
                "設定を取得できませんでした。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        try:
            channel = await process_apply_button(
                interaction.user,
                config,
            )
        except discord.HTTPException as exc:
            print(
                "[rainbowl] 入会申請処理に失敗しました:"
                f" guild_id={interaction.guild_id}"
                f" user_id={interaction.user.id}"
                f" error={exc}"
            )
            await interaction.followup.send(
                "入会申請の処理に失敗しました。"
                "運営へお問い合わせください。",
                ephemeral=True,
            )
            return

        if channel is None:
            await interaction.followup.send(
                "既に申請済みです。",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            "入会申請を受け付けました。"
            f"専用チャンネルをご確認ください → "
            f"{channel.mention}",
            ephemeral=True,
        )

    # ========================================
    # 運営向け：ボタン設置コマンド
    # ========================================

    @app_commands.command(
        name="rainbowl_setup_buttons",
        description=(
            "入会案内チャンネルへ「次へ」「入会申請」"
            "ボタンを設置します（運営専用・手動セットアップ用）"
        ),
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(
        administrator=True
    )
    @app_commands.checks.has_permissions(
        administrator=True
    )
    async def rainbowl_setup_buttons(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """
        入会案内チャンネル1〜6に「次へ」ボタン、
        チャンネル7に「入会申請」ボタンを新規メッセージとして投稿する。

        べき等ではない（再実行するとボタンが重複投稿される）ため、
        初期セットアップ時にのみ実行すること。
        """
        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        config = await self._get_config(
            interaction.guild_id
        )

        if config is None:
            await interaction.followup.send(
                "rainbowl設定を取得できませんでした。",
                ephemeral=True,
            )
            return

        if len(config.onboarding_channel_ids) < 2:
            await interaction.followup.send(
                "onboarding_channel_idsの設定が"
                "不正です（最低2件必要）。",
                ephemeral=True,
            )
            return

        posted_mentions = []

        for step, channel_id in enumerate(
            config.onboarding_channel_ids[:-1],
            start=1,
        ):
            channel = interaction.guild.get_channel(
                channel_id
            )

            if not isinstance(
                channel,
                discord.TextChannel,
            ):
                continue

            await channel.send(
                view=RainbowlNextButtonView(step)
            )
            posted_mentions.append(channel.mention)

        apply_channel = interaction.guild.get_channel(
            config.onboarding_channel_ids[-1]
        )

        if isinstance(apply_channel, discord.TextChannel):
            await apply_channel.send(
                view=RainbowlApplyButtonView()
            )
            posted_mentions.append(
                apply_channel.mention
            )

        if not posted_mentions:
            await interaction.followup.send(
                "対象チャンネルが見つかりませんでした。",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            "ボタンを設置しました: "
            + ", ".join(posted_mentions),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RainbowlOnboarding(bot))

    # 永続View：Bot起動のたびに再登録する
    for step in ONBOARDING_NEXT_BUTTON_STEPS:
        bot.add_view(RainbowlNextButtonView(step))

    bot.add_view(RainbowlApplyButtonView())
