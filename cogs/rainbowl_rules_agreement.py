# cogs/rainbowl_rules_agreement.py
"""
rainbowl機能：規約同意（⭕リアクション）と性別選択。

1. 「了解しました」ボタンで了承済みロールになった人が、
   規約・ルールの各チャンネルの同意用メッセージ（agreement_messages）の
   すべてに⭕を押すと、本人専用チャンネルに性別選択ボタンを投稿する。
2. 男性/女性ボタンを押すと、入会男性/入会女性ロールを付与する
   （押し直すと入れ替わる）。その性別のプロフィールチャンネルは
   入会男性/入会女性ロールに見えるよう、Discord側で権限を設定しておく。
3. プロフィールチャンネルへの初回投稿で新人ロール・プロフ記載済みロールが付く
   （cogs/rainbowl_profile_gate.py → services/rainbowl_onboarding_service.py）。

管理者コマンド：
- /set_agreement_reaction：実行したチャンネルの一番新しいメッセージに⭕を付け、同意用メッセージとして登録する

ロールIDは guild_config.rainbowl の entry_male_role_id / entry_female_role_id /
profile_done_role_id に直接設定する。
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from data.rainbowl.applicants_store import RainbowlStore
from services.rainbowl_config_service import (
    RainbowlConfigError,
    RainbowlConfigNotFoundError,
    RainbowlGuildConfig,
    get_rainbowl_config,
    set_rainbowl_fields,
)


AGREEMENT_EMOJI = "⭕"

ENTRY_GENDER_CUSTOM_ID_PREFIX = "rainbowl_entry_gender:"

GENDER_SELECT_TEXT = (
    "規約・ルールへの同意を確認しました。ありがとうございます！\n"
    "\n"
    "次に、以下のボタンから性別を選んでください。\n"
    "選んだ性別のプロフィールチャンネルが見えるようになります。\n"
    "（間違えた場合は、もう一方のボタンを押せば切り替わります）"
)

JST = timezone(timedelta(hours=9))

_applicants_store = RainbowlStore()


class EntryGenderButton(discord.ui.Button):
    """本人専用チャンネルの性別選択ボタン。"""

    def __init__(self, gender: str, label: str, style: discord.ButtonStyle):
        super().__init__(
            label=label,
            style=style,
            custom_id=f"{ENTRY_GENDER_CUSTOM_ID_PREFIX}{gender}",
        )
        self.gender = gender

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        cog = interaction.client.get_cog("RainbowlRulesAgreement")

        if cog is None:
            await interaction.response.send_message(
                "現在このボタンは利用できません。",
                ephemeral=True,
            )
            return

        await cog.handle_entry_gender_button(interaction, self.gender)


class EntryGenderView(discord.ui.View):
    """性別選択ボタンの永続View。"""

    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(
            EntryGenderButton("male", "男性", discord.ButtonStyle.primary)
        )
        self.add_item(
            EntryGenderButton("female", "女性", discord.ButtonStyle.danger)
        )


class RainbowlRulesAgreement(commands.Cog):
    """rainbowl：規約同意リアクションと性別選択。"""

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

    async def _has_reacted(
        self,
        guild: discord.Guild,
        channel_id: int,
        message_id: int,
        user_id: int,
    ) -> bool:
        """指定メッセージに、その人が⭕を押しているか。"""
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return False

        try:
            message = await channel.fetch_message(message_id)
        except discord.HTTPException:
            return False

        reaction = discord.utils.find(
            lambda r: str(r.emoji) == AGREEMENT_EMOJI,
            message.reactions,
        )
        if reaction is None:
            return False

        async for user in reaction.users():
            if user.id == user_id:
                return True
        return False

    # ========================================
    # ⭕リアクション
    # ========================================

    @commands.Cog.listener()
    async def on_raw_reaction_add(
        self,
        payload: discord.RawReactionActionEvent,
    ) -> None:
        if payload.guild_id is None:
            return
        if str(payload.emoji) != AGREEMENT_EMOJI:
            return

        member = payload.member
        if member is None or member.bot:
            return

        config = await self._get_config(payload.guild_id)
        if config is None:
            return
        if payload.message_id not in {
            message_id for _, message_id in config.agreement_messages
        }:
            return

        # 了承済みの人だけが対象（新人・正会員などが押しても何もしない）
        if config.acknowledged_role_id not in {role.id for role in member.roles}:
            return

        guild = member.guild

        try:
            for channel_id, message_id in config.agreement_messages:
                if message_id == payload.message_id:
                    continue
                if not await self._has_reacted(
                    guild, channel_id, message_id, member.id
                ):
                    return

            # ⭕を外して押し直しても、性別選択ボタンは1回だけ投稿する
            recorded = await asyncio.to_thread(
                _applicants_store.mark_rules_agreed,
                guild.id,
                member.id,
                datetime.now(JST).isoformat(),
            )
            if not recorded:
                return

            item = await asyncio.to_thread(
                _applicants_store.get_item,
                guild.id,
                member.id,
            )
            applicant_channel_id = item.get("applicant_channel_id")
            applicant_channel = (
                guild.get_channel(int(applicant_channel_id))
                if applicant_channel_id is not None
                else None
            )
            if applicant_channel is None:
                print(
                    "[rainbowl] 本人専用チャンネルを取得できず"
                    "性別選択ボタンを投稿できませんでした"
                    f" guild_id={guild.id} user_id={member.id}"
                )
                return

            await applicant_channel.send(
                content=member.mention,
                embed=discord.Embed(
                    description=GENDER_SELECT_TEXT,
                    color=discord.Color.light_grey(),
                ),
                view=EntryGenderView(),
            )
        except Exception as exc:
            print(
                "[rainbowl] 規約同意の処理に失敗しました:"
                f" guild_id={guild.id}"
                f" user_id={member.id}"
                f" error={exc}"
            )

    # ========================================
    # 性別選択ボタン
    # ========================================

    async def handle_entry_gender_button(
        self,
        interaction: discord.Interaction,
        gender: str,
    ) -> None:
        member = interaction.user
        if interaction.guild is None or not isinstance(member, discord.Member):
            await interaction.response.send_message(
                "サーバー内で使用してください。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        config = await self._get_config(interaction.guild_id)
        if config is None:
            await interaction.followup.send(
                "設定を取得できませんでした。",
                ephemeral=True,
            )
            return

        # プロフィール投稿前（了承済み）の本人だけが押せる
        if config.acknowledged_role_id not in {role.id for role in member.roles}:
            await interaction.followup.send(
                "このボタンは現在使用できません"
                "（対象外か、既に処理済みです）。",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        if gender == "male":
            role_id, other_role_id = config.entry_male_role_id, config.entry_female_role_id
            profile_channel_id = config.male_profile_channel_id
        else:
            role_id, other_role_id = config.entry_female_role_id, config.entry_male_role_id
            profile_channel_id = config.female_profile_channel_id

        role = guild.get_role(role_id) if role_id else None
        if role is None:
            print(
                "[rainbowl] 入会男性/入会女性ロールが見つかりません"
                f" guild_id={guild.id} gender={gender} role_id={role_id}"
            )
            await interaction.followup.send(
                "処理に失敗しました。"
                "お問い合わせ窓口から管理者へご連絡ください。",
                ephemeral=True,
            )
            return

        other_role = guild.get_role(other_role_id) if other_role_id else None

        try:
            if other_role is not None and other_role in member.roles:
                await member.remove_roles(
                    other_role,
                    reason="rainbowl: 入会時の性別を選び直し",
                )
            if role not in member.roles:
                await member.add_roles(
                    role,
                    reason="rainbowl: 入会時の性別を選択",
                )
        except discord.HTTPException as exc:
            print(
                "[rainbowl] 入会男性/入会女性ロールの付与に失敗しました"
                f" guild_id={guild.id} user_id={member.id} error={exc}"
            )
            await interaction.followup.send(
                "処理に失敗しました。"
                "お問い合わせ窓口から管理者へご連絡ください。",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"<#{profile_channel_id}> が見えるようになりました。\n"
            "プロフィールを投稿すると、ロール付与のチャンネルが見えるようになります。",
            ephemeral=True,
        )

    # ========================================
    # 管理者コマンド
    # ========================================

    @app_commands.command(
        name="set_agreement_reaction",
        description=(
            "このチャンネルの一番新しいメッセージに⭕を付け、"
            "規約同意用として登録します（管理者専用）"
        ),
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def set_agreement_reaction(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)

        config = await self._get_config(interaction.guild_id)
        if config is None:
            await interaction.followup.send(
                "このサーバーではこの機能は有効になっていません。",
                ephemeral=True,
            )
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send(
                "テキストチャンネルで実行してください。",
                ephemeral=True,
            )
            return

        message = None
        async for m in channel.history(limit=1):
            message = m
        if message is None:
            await interaction.followup.send(
                "このチャンネルにメッセージがありません。",
                ephemeral=True,
            )
            return

        await message.add_reaction(AGREEMENT_EMOJI)

        # 同じチャンネルで実行し直したら、そのチャンネルの登録を置き換える
        messages = {
            str(channel_id): str(message_id)
            for channel_id, message_id in config.agreement_messages
        }
        messages[str(channel.id)] = str(message.id)
        await set_rainbowl_fields(
            interaction.guild_id,
            {"agreement_messages": messages},
        )

        lines = [
            f"<#{channel_id}>：[メッセージ](https://discord.com/channels/"
            f"{interaction.guild_id}/{channel_id}/{message_id})"
            for channel_id, message_id in messages.items()
        ]
        await interaction.followup.send(
            f"{message.jump_url} に{AGREEMENT_EMOJI}を付けて、規約同意用に登録しました。\n"
            "現在の登録（すべてに押すと性別選択ボタンが届きます）：\n"
            + "\n".join(lines),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    bot.add_view(EntryGenderView())
    await bot.add_cog(RainbowlRulesAgreement(bot))
