# cogs/channel_cleanup.py
"""
実行したチャンネルのメッセージをまとめて削除する管理者用コマンド。

- /clear_messages を実行 → 確認ボタン → 削除
- 14日以内のメッセージはDiscordの一括削除APIで100件ずつまとめて消す
- 14日より古いメッセージは一括削除APIが使えないため1件ずつ消す（時間がかかる）
- 削除中のチャンネルは削除ログを出さない（大量に流れるのを防ぐ）
"""

import discord
from discord import app_commands
from discord.ext import commands

from services import server_log_service

# 一括削除できるチャンネル種別（VCインチャ・スレッドを含む）
PURGEABLE_CHANNEL_TYPES = (
    discord.TextChannel,
    discord.VoiceChannel,
    discord.StageChannel,
    discord.Thread,
)


class ClearMessagesConfirmView(discord.ui.View):
    def __init__(self, invoker_id: int, channel):
        super().__init__(timeout=60)
        self.invoker_id = invoker_id
        self.channel = channel

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                "このボタンはコマンドを実行した人だけが押せます。",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="削除する", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.stop()
        await interaction.response.edit_message(
            content="🗑️ 削除中です…（古いメッセージが多いと時間がかかります）",
            view=None,
        )

        channel = self.channel
        server_log_service.PURGING_CHANNEL_IDS.add(channel.id)
        try:
            deleted = await channel.purge(
                limit=None,
                bulk=True,
                reason=f"/clear_messages by {interaction.user} ({interaction.user.id})",
            )
        except discord.Forbidden:
            await self._report(
                interaction,
                "❌ 削除する権限がありません。Botに「メッセージの管理」と"
                "「メッセージ履歴を読む」権限があるか確認してください。",
            )
            return
        except discord.HTTPException as exc:
            await self._report(
                interaction, f"❌ 削除中にエラーが発生しました: {exc}"
            )
            return
        finally:
            server_log_service.PURGING_CHANNEL_IDS.discard(channel.id)

        await self._report(
            interaction, f"✅ {len(deleted)} 件のメッセージを削除しました。"
        )

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.stop()
        await interaction.response.edit_message(
            content="キャンセルしました。", view=None
        )

    @staticmethod
    async def _report(interaction: discord.Interaction, content: str) -> None:
        # 削除に15分以上かかるとインタラクションの期限が切れて編集できない
        try:
            await interaction.edit_original_response(content=content)
        except discord.HTTPException:
            print(f"[channel_cleanup] {content}")


class ChannelCleanupCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="clear_messages",
        description="このチャンネルのメッセージをすべて削除します（管理者用）",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def clear_messages(self, interaction: discord.Interaction):
        channel = interaction.channel
        if not isinstance(channel, PURGEABLE_CHANNEL_TYPES):
            await interaction.response.send_message(
                "このチャンネルでは使えません。", ephemeral=True
            )
            return

        if channel.id in server_log_service.PURGING_CHANNEL_IDS:
            await interaction.response.send_message(
                "このチャンネルは現在削除中です。", ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"⚠️ {channel.mention} のメッセージを**すべて**削除します。"
            "元に戻せません。実行してよろしいですか？",
            view=ClearMessagesConfirmView(interaction.user.id, channel),
            ephemeral=True,
        )

    @clear_messages.error
    async def clear_messages_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "このコマンドは管理者のみ実行できます。", ephemeral=True
            )
            return
        raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(ChannelCleanupCog(bot))
