# cogs/rainbowl_interview.py
"""
rainbowl機能：「受付」リアクション検知・プロフィール転記・/ok /ng コマンド。

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
    build_verdict_embed,
    process_acknowledge_passed_button,
    process_pass_verdict,
    process_profile_candidate_message,
    process_reception_reaction,
    process_reject_verdict,
    resolve_applicant_from_channel,
)


ACKNOWLEDGE_PASSED_BUTTON_CUSTOM_ID = "rainbowl_acknowledge_passed"


class AcknowledgePassedButton(discord.ui.Button):
    """合格通知メッセージに設置する「了解しました」ボタン。"""

    def __init__(self):
        super().__init__(
            label="了解しました",
            style=discord.ButtonStyle.success,
            custom_id=ACKNOWLEDGE_PASSED_BUTTON_CUSTOM_ID,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        cog = interaction.client.get_cog(
            "RainbowlInterview"
        )

        if cog is None:
            await interaction.response.send_message(
                "現在このボタンは利用できません。",
                ephemeral=True,
            )
            return

        await cog.handle_acknowledge_passed_button(
            interaction
        )


class RainbowlAcknowledgePassedView(discord.ui.View):
    """「了解しました」ボタンの永続View。"""

    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(AcknowledgePassedButton())


class PassVerdictModal(discord.ui.Modal):
    """`/ok`実行後に表示する、合格コメント入力モーダル。"""

    def __init__(
        self,
        member: discord.Member,
        config: RainbowlGuildConfig,
    ):
        super().__init__(
            title="合格コメント",
            timeout=600,
        )

        self.member = member
        self.config = config

        self.comment_input = discord.ui.TextInput(
            label="コメント（任意）",
            required=False,
            max_length=1000,
            style=discord.TextStyle.paragraph,
        )

        self.add_item(self.comment_input)

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        comment = self.comment_input.value.strip()

        try:
            await process_pass_verdict(
                self.member,
                comment,
                self.config,
                notice_view=RainbowlAcknowledgePassedView(),
            )
        except Exception as exc:
            print(
                "[rainbowl] /ok確定処理に失敗しました:"
                f" guild_id={self.member.guild.id}"
                f" user_id={self.member.id} error={exc}"
            )
            await interaction.followup.send(
                "合格処理に失敗しました。",
                ephemeral=True,
            )
            return

        await _post_verdict_record(
            guild=self.member.guild,
            config=self.config,
            member=self.member,
            passed=True,
            reason_or_comment=comment,
        )

        await interaction.followup.send(
            f"{self.member.mention} を"
            "合格として処理しました。",
            ephemeral=True,
        )


class RejectVerdictModal(discord.ui.Modal):
    """`/ng`実行後に表示する、不合格理由入力モーダル。"""

    def __init__(
        self,
        member: discord.Member,
        config: RainbowlGuildConfig,
    ):
        super().__init__(
            title="不合格理由",
            timeout=600,
        )

        self.member = member
        self.config = config

        self.reason_input = discord.ui.TextInput(
            label="理由（必須）",
            required=True,
            max_length=1000,
            style=discord.TextStyle.paragraph,
        )

        self.add_item(self.reason_input)

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        reason = self.reason_input.value.strip()

        if not reason:
            await interaction.followup.send(
                "理由を入力してください。",
                ephemeral=True,
            )
            return

        try:
            executed = await process_reject_verdict(
                self.member,
                reason,
                self.config,
            )
        except Exception as exc:
            print(
                "[rainbowl] /ng確定処理に失敗しました:"
                f" guild_id={self.member.guild.id}"
                f" user_id={self.member.id} error={exc}"
            )
            await interaction.followup.send(
                "不合格処理に失敗しました。",
                ephemeral=True,
            )
            return

        if not executed:
            await interaction.followup.send(
                "既に合格処理済みのため、"
                "不合格処理を中断しました。",
                ephemeral=True,
            )
            return

        await _post_verdict_record(
            guild=self.member.guild,
            config=self.config,
            member=self.member,
            passed=False,
            reason_or_comment=reason,
        )

        await interaction.followup.send(
            f"{self.member.mention} を"
            "不合格として処理しました。",
            ephemeral=True,
        )


async def _post_verdict_record(
    guild: discord.Guild,
    config: RainbowlGuildConfig,
    member: discord.Member,
    passed: bool,
    reason_or_comment: str,
) -> None:
    """合否記録チャンネルへEmbedを投稿する。"""
    review_results_channel = guild.get_channel(
        config.review_results_channel_id
    )

    if review_results_channel is None:
        print(
            "[rainbowl] 合否記録チャンネルを"
            f"取得できません guild_id={guild.id}"
        )
        return

    embed = build_verdict_embed(
        member,
        passed,
        reason_or_comment,
    )

    try:
        await review_results_channel.send(embed=embed)
    except discord.HTTPException as exc:
        print(
            "[rainbowl] 合否記録の投稿に失敗しました"
            f" guild_id={guild.id} error={exc}"
        )


class RainbowlInterview(commands.Cog):
    """rainbowl：面接用プロフィールの検知〜合否判定コマンド。"""

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
    # 面接用プロフィール検知
    # ========================================

    @commands.Cog.listener()
    async def on_message(
        self,
        message: discord.Message,
    ) -> None:
        if message.guild is None:
            return

        if message.author.bot:
            return

        config = await self._get_config(
            message.guild.id
        )

        if config is None:
            return

        try:
            await process_profile_candidate_message(
                message,
                config,
            )
        except Exception as exc:
            print(
                "[rainbowl] プロフィール検知処理に"
                "失敗しました:"
                f" guild_id={message.guild.id}"
                f" user_id={message.author.id}"
                f" error={exc}"
            )

    # ========================================
    # 「受付」スタンプ承認
    # ========================================

    @commands.Cog.listener()
    async def on_raw_reaction_add(
        self,
        payload: discord.RawReactionActionEvent,
    ) -> None:
        if payload.guild_id is None:
            return

        config = await self._get_config(
            payload.guild_id
        )

        if config is None:
            return

        try:
            await process_reception_reaction(
                payload,
                self.bot,
                config,
            )
        except Exception as exc:
            print(
                "[rainbowl] 受付リアクション処理に"
                "失敗しました:"
                f" guild_id={payload.guild_id}"
                f" error={exc}"
            )

    # ========================================
    # 「了解しました」ボタン（合格通知）
    # ========================================

    async def handle_acknowledge_passed_button(
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
            executed = await process_acknowledge_passed_button(
                interaction.user,
                config,
            )
        except Exception as exc:
            print(
                "[rainbowl] 新人移行処理に失敗しました:"
                f" guild_id={interaction.guild_id}"
                f" user_id={interaction.user.id}"
                f" error={exc}"
            )
            await interaction.followup.send(
                "処理に失敗しました。"
                "運営へお問い合わせください。",
                ephemeral=True,
            )
            return

        if not executed:
            await interaction.followup.send(
                "このボタンは現在使用できません"
                "（対象外か、既に処理済みです）。",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            "ありがとうございます！"
            "新人ロールを付与しました。",
            ephemeral=True,
        )

    # ========================================
    # 合否判定コマンド
    # ========================================

    async def _prepare_verdict_target(
        self,
        interaction: discord.Interaction,
    ) -> Optional[
        tuple[discord.Member, RainbowlGuildConfig]
    ]:
        """
        /ok /ng共通の事前検証。

        実行者がstaff_role_idを持ち、実行チャンネルが
        対象応募者のapplicant_channel_idと一致する場合のみ
        (応募者Member, config) を返す。
        """
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
            return None

        config = await self._get_config(
            interaction.guild_id
        )

        if config is None:
            await interaction.response.send_message(
                "rainbowl設定を取得できませんでした。",
                ephemeral=True,
            )
            return None

        staff_role = interaction.guild.get_role(
            config.staff_role_id
        )

        if (
            staff_role is None
            or staff_role not in interaction.user.roles
        ):
            await interaction.response.send_message(
                "このコマンドは運営専用です。",
                ephemeral=True,
            )
            return None

        if not isinstance(
            interaction.channel,
            discord.TextChannel,
        ):
            await interaction.response.send_message(
                "本人専用チャンネルで実行してください。",
                ephemeral=True,
            )
            return None

        applicant = await resolve_applicant_from_channel(
            interaction.channel,
            config,
        )

        if applicant is None:
            await interaction.response.send_message(
                "このチャンネルを対象の本人専用チャンネルとして"
                "認識できませんでした。",
                ephemeral=True,
            )
            return None

        return applicant, config

    @app_commands.command(
        name="ok",
        description="面談の合格処理を行います（運営専用）",
    )
    @app_commands.guild_only()
    async def rainbowl_ok(
        self,
        interaction: discord.Interaction,
    ) -> None:
        result = await self._prepare_verdict_target(
            interaction
        )

        if result is None:
            return

        applicant, config = result

        await interaction.response.send_modal(
            PassVerdictModal(applicant, config)
        )

    @app_commands.command(
        name="ng",
        description="面談の不合格処理を行います（運営専用）",
    )
    @app_commands.guild_only()
    async def rainbowl_ng(
        self,
        interaction: discord.Interaction,
    ) -> None:
        result = await self._prepare_verdict_target(
            interaction
        )

        if result is None:
            return

        applicant, config = result

        await interaction.response.send_modal(
            RejectVerdictModal(applicant, config)
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RainbowlInterview(bot))

    # 永続View：Bot起動のたびに再登録する
    bot.add_view(RainbowlAcknowledgePassedView())
