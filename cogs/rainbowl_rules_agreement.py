# cogs/rainbowl_rules_agreement.py
"""
rainbowl機能：規約同意（⭕リアクション）。

「了解しました」ボタンで了承済みロールになった人が、
規約・ルールの各チャンネルの同意用メッセージ（agreement_messages）の
すべてに⭕を押した時点で、規約同意済みロール（agreed_role_id）を付与する。
ロール付与チャンネルは規約同意済みロールに見えるよう、Discord側で権限を設定しておく。

規約同意済みロールは、プロフィールチャンネルへの初回投稿で新人になる時に外す
（services/rainbowl_onboarding_service.py の process_profile_channel_post）。

管理者コマンド：
- /set_agreement_reaction：実行したチャンネルの一番新しいメッセージに⭕を付け、同意用メッセージとして登録する

規約同意済みロールは guild_config.rainbowl.agreed_role_id に直接設定する。
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
    set_rainbowl_fields,
)


AGREEMENT_EMOJI = "⭕"


class RainbowlRulesAgreement(commands.Cog):
    """rainbowl：規約同意リアクション。"""

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
        if config is None or not config.agreed_role_id:
            return
        if payload.message_id not in {
            message_id for _, message_id in config.agreement_messages
        }:
            return

        guild = member.guild
        role_ids = {role.id for role in member.roles}

        # 了承済みの人だけが対象（新人・正会員などが押しても何もしない）
        if config.acknowledged_role_id not in role_ids:
            return
        if config.agreed_role_id in role_ids:
            return

        try:
            for channel_id, message_id in config.agreement_messages:
                if message_id == payload.message_id:
                    continue
                if not await self._has_reacted(
                    guild, channel_id, message_id, member.id
                ):
                    return

            agreed_role = guild.get_role(config.agreed_role_id)
            if agreed_role is None:
                print(
                    "[rainbowl] 規約同意済みロールが見つかりません"
                    f" guild_id={guild.id}"
                    f" role_id={config.agreed_role_id}"
                )
                return

            await member.add_roles(
                agreed_role,
                reason="rainbowl: 規約・ルールに同意",
            )
        except Exception as exc:
            print(
                "[rainbowl] 規約同意の処理に失敗しました:"
                f" guild_id={guild.id}"
                f" user_id={member.id}"
                f" error={exc}"
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
            "現在の登録（すべてに押すと規約同意済みになります）：\n"
            + "\n".join(lines),
            ephemeral=True,
        )

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RainbowlRulesAgreement(bot))
