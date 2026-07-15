from __future__ import annotations

from typing import Optional

import discord
from discord.ext import commands


TRIGGER_PHRASE = "こいよ"

# Discord側は最大500文字まで対応しているが、
# サーバー内で見やすいように80文字へ制限
STATUS_MAX_LENGTH = 80

PANEL_TIMEOUT_SECONDS = 180


class VoiceStatusModal(discord.ui.Modal):
    """VCステータス入力用モーダル"""

    def __init__(
        self,
        cog: "VoiceStatusCog",
        requester_id: int,
        voice_channel_id: int,
    ):
        super().__init__(
            title="VCステータスを変更",
            timeout=PANEL_TIMEOUT_SECONDS,
        )

        self.cog = cog
        self.requester_id = requester_id
        self.voice_channel_id = voice_channel_id

        self.status_input = discord.ui.TextInput(
            label="ステータス",
            placeholder="80文字までなら許してやる",
            required=True,
            max_length=STATUS_MAX_LENGTH,
        )

        self.add_item(self.status_input)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "このパネルは、ZeroBotを呼び出した本人だけが操作できるよ。",
                ephemeral=True,
            )
            return

        if interaction.guild is None:
            await interaction.response.send_message(
                "サーバー内で実行してね。",
                ephemeral=True,
            )
            return

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "メンバー情報を取得できなかったよ。",
                ephemeral=True,
            )
            return

        voice_state = interaction.user.voice

        if (
            voice_state is None
            or voice_state.channel is None
            or voice_state.channel.id != self.voice_channel_id
        ):
            await interaction.response.send_message(
                "呼び出したボイスチャンネルに参加している間だけ変更できるよ。",
                ephemeral=True,
            )
            return

        channel = interaction.guild.get_channel(self.voice_channel_id)

        if not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message(
                "対象のボイスチャンネルが見つからなかったよ。",
                ephemeral=True,
            )
            return

        status = self.status_input.value.strip()

        if not status:
            await interaction.response.send_message(
                "ステータスを入力してね。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        result = await self.cog.change_voice_status(
            channel=channel,
            status=status,
            changed_by=interaction.user,
        )

        if result:
            await interaction.followup.send(
                f"✅ {channel.mention} のステータスを変更したよ。\n"
                f"**{status}**",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "❌ ステータスを変更できなかったよ。\n"
                "ZeroBotに「ボイスチャンネルステータスを設定」の権限があるか確認してね。",
                ephemeral=True,
            )


class VoiceStatusView(discord.ui.View):
    """ステータス変更パネル"""

    def __init__(
        self,
        cog: "VoiceStatusCog",
        requester_id: int,
        voice_channel_id: int,
    ):
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)

        self.cog = cog
        self.requester_id = requester_id
        self.voice_channel_id = voice_channel_id
        self.message: Optional[discord.Message] = None

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "このパネルは、ZeroBotを呼び出した本人だけが操作できるよ。",
                ephemeral=True,
            )
            return False

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "メンバー情報を取得できなかったよ。",
                ephemeral=True,
            )
            return False

        voice_state = interaction.user.voice

        if (
            voice_state is None
            or voice_state.channel is None
            or voice_state.channel.id != self.voice_channel_id
        ):
            await interaction.response.send_message(
                "呼び出したボイスチャンネルに参加している間だけ操作できるよ。",
                ephemeral=True,
            )
            return False

        return True

    @discord.ui.button(
        label="ステータス変更",
        style=discord.ButtonStyle.primary,
        emoji="✏️",
    )
    async def change_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        modal = VoiceStatusModal(
            cog=self.cog,
            requester_id=self.requester_id,
            voice_channel_id=self.voice_channel_id,
        )

        await interaction.response.send_modal(modal)

    @discord.ui.button(
        label="ステータス解除",
        style=discord.ButtonStyle.secondary,
        emoji="🧹",
    )
    async def clear_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if interaction.guild is None:
            await interaction.response.send_message(
                "サーバー内で実行してね。",
                ephemeral=True,
            )
            return

        channel = interaction.guild.get_channel(self.voice_channel_id)

        if not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message(
                "対象のボイスチャンネルが見つからなかったよ。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        result = await self.cog.change_voice_status(
            channel=channel,
            status=None,
            changed_by=interaction.user,
        )

        if result:
            await interaction.followup.send(
                f"✅ {channel.mention} のステータスを解除したよ。",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "❌ ステータスを解除できなかったよ。\n"
                "ZeroBotの権限を確認してね。",
                ephemeral=True,
            )

    async def on_timeout(self):
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class VoiceStatusCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def change_voice_status(
        self,
        channel: discord.VoiceChannel,
        status: Optional[str],
        changed_by: discord.abc.User,
    ) -> bool:
        """VCステータスを変更する"""

        try:
            await channel.edit(
                status=status,
                reason=f"ZeroBot voice status: {changed_by} ({changed_by.id})",
            )
            return True

        except discord.Forbidden:
            print(
                "❌ VCステータス変更権限がありません: "
                f"guild={channel.guild.id}, channel={channel.id}"
            )
            return False

        except discord.HTTPException as error:
            print(
                "❌ VCステータス変更に失敗しました: "
                f"guild={channel.guild.id}, "
                f"channel={channel.id}, "
                f"error={error}"
            )
            return False

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Botの投稿やDMは対象外
        if message.author.bot or message.guild is None:
            return

        # 完全一致のみ反応
        if message.content.strip() != TRIGGER_PHRASE:
            return

        if not isinstance(message.author, discord.Member):
            return

        voice_state = message.author.voice

        if voice_state is None or voice_state.channel is None:
            await message.reply(
                "先にボイスチャンネルへ参加してから呼んでね。",
                mention_author=False,
            )
            return

        voice_channel = voice_state.channel

        if not isinstance(voice_channel, discord.VoiceChannel):
            await message.reply(
                "通常のボイスチャンネルで使ってね。",
                mention_author=False,
            )
            return

        # 所属しているVCのチャットでだけ呼び出せる
        if message.channel.id != voice_channel.id:
            await message.reply(
                f"{voice_channel.mention} のチャットで"
                f"「{TRIGGER_PHRASE}」と呼んでね。",
                mention_author=False,
            )
            return

        view = VoiceStatusView(
            cog=self,
            requester_id=message.author.id,
            voice_channel_id=voice_channel.id,
        )

        embed = discord.Embed(
            title="ZERO BOT、参上。",
            description=(
                f"{voice_channel.mention} のステータスを変更できるよ。\n\n"
                "下のボタンから操作してね。"
            ),
            color=discord.Color.blurple(),
        )

        embed.set_footer(
            text="呼び出した本人のみ操作可能・3分で操作期限切れ"
        )

        panel_message = await message.reply(
            embed=embed,
            view=view,
            mention_author=False,
        )

        view.message = panel_message


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceStatusCog(bot))
