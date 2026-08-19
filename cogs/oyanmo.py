import discord
from discord import app_commands
from discord.ext import commands

from typing import Awaitable, Callable, List, Optional

from utils.helpers import get_voice_connected_members
from utils.messages import get_random_success_message

# ★ DB からギルド設定を取るための Store
from data.guild_config_store import GuildConfigStore

# グローバルに1個だけ作って使い回し
guild_config_store = GuildConfigStore()

OYANMO_OPEN_BUTTON_CUSTOM_ID = "oyanmo:open_button"
SELECT_MAX_OPTIONS = 25


def get_oyanmo_config(guild_id: int) -> dict:
    """
    ギルドごとのおやんも設定を DynamoDB から取得。
    何もなければ空 dict を返す。
    """
    config = guild_config_store.get_config(guild_id) or {}
    return config.get("oyanmo") or {}


def _get_channel_from_config(guild: discord.Guild, key: str) -> Optional[discord.abc.GuildChannel]:
    oyanmo_cfg = get_oyanmo_config(guild.id)
    raw_id = oyanmo_cfg.get(key)
    if not raw_id:
        return None

    try:
        chan_id = int(raw_id)
    except (TypeError, ValueError):
        return None

    return guild.get_channel(chan_id)


def get_target_voice_channel(guild: discord.Guild) -> Optional[discord.VoiceChannel]:
    """おやんも設定から移動先VC（target_voice_channel_id）を取得する。"""
    channel = _get_channel_from_config(guild, "target_voice_channel_id")
    return channel if isinstance(channel, discord.VoiceChannel) else None


def get_log_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    """「〇〇が〇〇を飛ばしました」ログの投稿先チャンネル（log_channel_id）を取得する。"""
    channel = _get_channel_from_config(guild, "log_channel_id")
    return channel if isinstance(channel, discord.TextChannel) else None


def get_button_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    """常設おやんもボタンの設置先チャンネル（button_channel_id）を取得する。"""
    channel = _get_channel_from_config(guild, "button_channel_id")
    return channel if isinstance(channel, discord.TextChannel) else None


class OyanmoUserSelect(discord.ui.Select):
    """VC接続者から飛ばす相手を選ぶセレクト（複数選択可）。"""

    def __init__(self, candidates: List[discord.Member]):
        limited = candidates[:SELECT_MAX_OPTIONS]
        options = [
            discord.SelectOption(label=m.display_name, value=str(m.id))
            for m in limited
        ]
        super().__init__(
            placeholder="飛ばす相手を選択（複数可）",
            min_values=1,
            max_values=len(options),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()


class OyanmoSelectView(discord.ui.View):
    """おやんも実行用の選択パネル（Select + 決定/キャンセル）。"""

    def __init__(
        self,
        *,
        candidates: List[discord.Member],
        target_channel: discord.VoiceChannel,
        post_channel: discord.abc.Messageable,
        log_channel: Optional[discord.TextChannel],
        requester: discord.Member,
        on_success: Optional[Callable[[], Awaitable[None]]] = None,
        timeout: float = 180,
    ):
        super().__init__(timeout=timeout)
        self.target_channel = target_channel
        self.post_channel = post_channel
        self.log_channel = log_channel
        self.requester = requester
        self.on_success = on_success

        self._candidates_by_id = {str(m.id): m for m in candidates}

        self.select = OyanmoUserSelect(candidates)
        self.add_item(self.select)

    @discord.ui.button(label="決定", style=discord.ButtonStyle.success, row=1)
    async def decide_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        targets = [
            self._candidates_by_id[v]
            for v in self.select.values
            if v in self._candidates_by_id
        ]

        if not targets:
            await interaction.response.send_message(
                "飛ばす相手を選択してください。", ephemeral=True
            )
            return

        await interaction.response.defer()

        moved: List[discord.Member] = []
        for member in targets:
            try:
                await member.move_to(self.target_channel)
                moved.append(member)
            except discord.HTTPException:
                continue

        if not moved:
            await interaction.edit_original_response(
                content="❌ 移動に失敗しました（対象がVCから外れた可能性があります）。",
                view=None,
            )
            self.stop()
            return

        for member in moved:
            embed = discord.Embed(
                title="おやんも実行",
                description=get_random_success_message(
                    self.requester.guild.id, member.display_name
                ),
                color=0x32CD32,
            )
            await self.post_channel.send(embed=embed)

        if self.log_channel is not None:
            mentions = " ".join(m.mention for m in moved)
            await self.log_channel.send(
                f"{self.requester.mention} が {mentions} を飛ばしました"
            )

        if self.on_success is not None:
            await self.on_success()

        await interaction.edit_original_response(content="✅ 実行しました。", view=None)
        self.stop()

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary, row=1)
    async def cancel_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.edit_message(content="キャンセルしました。", view=None)
        self.stop()


class OyanmoOpenButton(discord.ui.Button):
    """常設チャンネルに設置する「おやんも」ボタン。"""

    def __init__(self) -> None:
        super().__init__(
            label="おやんも",
            style=discord.ButtonStyle.primary,
            emoji="😴",
            custom_id=OYANMO_OPEN_BUTTON_CUSTOM_ID,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("OyanmoCog")

        if cog is None:
            await interaction.response.send_message(
                "現在この機能は利用できません。", ephemeral=True
            )
            return

        await cog.handle_open_button(interaction)


class OyanmoOpenButtonView(discord.ui.View):
    """「おやんも」ボタンの永続View。"""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(OyanmoOpenButton())


class OyanmoCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def handle_open_button(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "サーバー内で実行してください。", ephemeral=True
            )
            return

        candidates = get_voice_connected_members(guild)
        if not candidates:
            await interaction.response.send_message(
                "現在VCに接続しているユーザーがいません。", ephemeral=True
            )
            return

        target_channel = get_target_voice_channel(guild)
        if target_channel is None:
            await interaction.response.send_message(
                "❌ おやんも移動先VCが設定されていません。\n"
                "管理者さんに `target_voice_channel_id` の設定をお願いしてください。",
                ephemeral=True,
            )
            return

        log_channel = get_log_channel(guild)
        original_message = interaction.message
        button_channel = (
            original_message.channel
            if original_message is not None
            else interaction.channel
        )

        async def repost_button() -> None:
            if original_message is not None:
                try:
                    await original_message.delete()
                except discord.HTTPException:
                    pass

            if isinstance(button_channel, discord.TextChannel):
                await button_channel.send(view=OyanmoOpenButtonView())

        view = OyanmoSelectView(
            candidates=candidates,
            target_channel=target_channel,
            post_channel=button_channel,
            log_channel=log_channel,
            requester=interaction.user,
            on_success=repost_button,
        )
        await interaction.response.send_message(view=view, ephemeral=True)

    @app_commands.command(
        name="set_oyanmo",
        description="常設のおやんもボタンを設定チャンネルへ設置します（管理者専用）",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_button(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        channel = get_button_channel(guild)

        if channel is None:
            await interaction.followup.send(
                "❌ 設置先チャンネルが見つかりません。\n"
                "`oyanmo.button_channel_id` の設定を確認してください。",
                ephemeral=True,
            )
            return

        await channel.send(view=OyanmoOpenButtonView())
        await interaction.followup.send(
            f"✅ {channel.mention} にボタンを設置しました。", ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(OyanmoCog(bot))

    # 永続View：Bot起動のたびに再登録する
    bot.add_view(OyanmoOpenButtonView())
