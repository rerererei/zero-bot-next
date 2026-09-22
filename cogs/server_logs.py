# cogs/server_logs.py
"""
全ギルド共通のサーバーログ機能。

- 管理ログ：ロールの付与・剥奪
- 編集ログ：テキストチャンネル・VCインチャ・スレッドでのメッセージ編集
- 削除ログ：テキストチャンネル・VCインチャ・スレッドでのメッセージ削除
- VCログ：ボイスチャンネルへの入退室
- 退出ログ：サーバーからの脱退（自主退出・Kick・Banを区別せずまとめて記録）

Botユーザーのメッセージ・ロールは対象外（人間の操作のみ記録する）。
投稿先チャンネルは guild_config["server_logs"] にギルドごとに設定し、
未設定のギルドには一切影響しない。
"""

import discord
from discord import app_commands
from discord.ext import commands

from services import server_log_service


class ServerLogsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ========================================
    # 管理ログ：ロールの付与・剥奪
    # ========================================
    @commands.Cog.listener()
    async def on_member_update(
        self, before: discord.Member, after: discord.Member
    ) -> None:
        if after.bot:
            return

        before_roles = set(before.roles)
        after_roles = set(after.roles)
        if before_roles == after_roles:
            return

        added = [
            role
            for role in (after_roles - before_roles)
            if not role.is_default()
        ]
        removed = [
            role
            for role in (before_roles - after_roles)
            if not role.is_default()
        ]
        if not added and not removed:
            return

        try:
            await server_log_service.send_role_change_log(
                after, added, removed
            )
        except Exception as exc:
            print(f"[server_logs] role change log error: {exc}")

    # ========================================
    # 編集ログ
    # ========================================
    @commands.Cog.listener()
    async def on_message_edit(
        self, before: discord.Message, after: discord.Message
    ) -> None:
        if after.guild is None:
            return
        if after.author.bot:
            return
        if not isinstance(
            after.channel, server_log_service.LOGGABLE_CHANNEL_TYPES
        ):
            return
        if before.content == after.content:
            return

        try:
            await server_log_service.send_message_edit_log(before, after)
        except Exception as exc:
            print(f"[server_logs] message edit log error: {exc}")

    # ========================================
    # 削除ログ
    # ========================================
    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        if message.author.bot:
            return
        if not isinstance(
            message.channel, server_log_service.LOGGABLE_CHANNEL_TYPES
        ):
            return

        try:
            await server_log_service.send_message_delete_log(message)
        except Exception as exc:
            print(f"[server_logs] message delete log error: {exc}")

    # ========================================
    # VCログ：入退室
    # ========================================
    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot:
            return
        if before.channel == after.channel:
            return

        try:
            if before.channel is not None:
                await server_log_service.send_voice_state_log(
                    member, before.channel, joined=False
                )
            if after.channel is not None:
                await server_log_service.send_voice_state_log(
                    member, after.channel, joined=True
                )
        except Exception as exc:
            print(f"[server_logs] voice state log error: {exc}")

    # ========================================
    # 退出ログ
    # ========================================
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.bot:
            return

        try:
            await server_log_service.send_member_leave_log(member)
        except Exception as exc:
            print(f"[server_logs] member leave log error: {exc}")

    # ========================================
    # 管理者コマンド
    # ========================================
    @app_commands.command(
        name="set_server_log_channel",
        description="サーバーログの投稿先チャンネルを設定します（管理者専用）",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        log_type="ログの種別", channel="投稿先のテキストチャンネル"
    )
    @app_commands.choices(
        log_type=[
            app_commands.Choice(name=label, value=value)
            for label, value in server_log_service.LOG_TYPE_CHOICES
        ]
    )
    async def set_server_log_channel(
        self,
        interaction: discord.Interaction,
        log_type: app_commands.Choice[str],
        channel: discord.TextChannel,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        await server_log_service.set_log_channel(
            interaction.guild_id, log_type.value, channel.id
        )
        await interaction.followup.send(
            f"{log_type.name}の投稿先を {channel.mention} に設定しました。",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ServerLogsCog(bot))
