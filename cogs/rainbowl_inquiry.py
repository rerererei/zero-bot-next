# cogs/rainbowl_inquiry.py
"""
rainbowl：お問い合わせ窓口。

- お問い合わせチャンネルに「問い合わせる」ボタンを設置する（/set_inquiry_button）
- ボタンを押すとモーダルで件名・内容を入力
- 送信すると、お問い合わせチャンネルにプライベートスレッドを作成し、
  本人を追加して、通知ロール（管理者・Supporter）をメンションする
  （両ロールはAdministrator権限を持つため、プライベートスレッドも閲覧できる）
- 以降のやり取りはそのスレッドで行う

設定は zero_bot_guild_config の inquiry 名前空間（任意）：
    {"channel_id": "...", "notify_role_ids": ["...", ...]}
未設定のギルドでは何もしない。
"""

import asyncio
from dataclasses import dataclass
from typing import Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from data.guild_config_store import GuildConfigStore
from services import rainbowl_texts
from utils.interaction_cooldown import is_on_cooldown


INQUIRY_OPEN_CUSTOM_ID = "rainbowl_inquiry_open"

# 送信の連打で同じ問い合わせスレッドが複数できるのを防ぐ
SUBMIT_COOLDOWN_SECONDS = 30.0

THREAD_NAME_MAX = 100

_guild_config_store = GuildConfigStore()


@dataclass(frozen=True)
class InquiryConfig:
    channel_id: int
    notify_role_ids: Tuple[int, ...]


async def get_inquiry_config(guild_id: int) -> Optional[InquiryConfig]:
    guild_config = await asyncio.to_thread(
        _guild_config_store.get_config,
        guild_id,
    )
    raw = (guild_config or {}).get("inquiry")

    if not isinstance(raw, dict):
        return None

    try:
        return InquiryConfig(
            channel_id=int(raw["channel_id"]),
            notify_role_ids=tuple(
                int(x) for x in raw.get("notify_role_ids") or []
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        print(
            "[inquiry] inquiry設定が不正です:"
            f" guild_id={guild_id} error={exc}"
        )
        return None


class InquiryModal(discord.ui.Modal):
    """件名・内容の入力モーダル。"""

    def __init__(self, config: InquiryConfig):
        super().__init__(title="お問い合わせ", timeout=900)
        self.config = config

        self.subject_input = discord.ui.TextInput(
            label="件名",
            placeholder="例：ロールについて",
            required=True,
            max_length=50,
        )
        self.body_input = discord.ui.TextInput(
            label="内容",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1500,
        )

        self.add_item(self.subject_input)
        self.add_item(self.body_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        member = interaction.user
        guild = interaction.guild

        if guild is None or not isinstance(member, discord.Member):
            await interaction.response.send_message(
                "サーバー内で使用してください。", ephemeral=True
            )
            return

        if is_on_cooldown(
            ("inquiry_submit", guild.id, member.id),
            SUBMIT_COOLDOWN_SECONDS,
        ):
            await interaction.response.send_message(
                "少し時間をおいてから、もう一度送信してください。",
                ephemeral=True,
            )
            return

        channel = guild.get_channel(self.config.channel_id)

        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "お問い合わせチャンネルが見つかりません。管理者へ直接ご連絡ください。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        subject = self.subject_input.value.strip()
        body = self.body_input.value.strip()

        try:
            thread = await _create_inquiry_thread(
                channel,
                member,
                self.config,
                subject,
                body,
            )
        except discord.HTTPException as exc:
            print(
                "[inquiry] 問い合わせスレッドの作成に失敗しました:"
                f" guild_id={guild.id} user_id={member.id} error={exc}"
            )
            await interaction.followup.send(
                "送信に失敗しました。時間をおいてもう一度お試しください。",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"お問い合わせを受け付けました。続きはこちらのスレッドでやり取りします → {thread.mention}",
            ephemeral=True,
        )


async def _create_inquiry_thread(
    channel: discord.TextChannel,
    member: discord.Member,
    config: InquiryConfig,
    subject: str,
    body: str,
) -> discord.Thread:
    """本人＋管理者（管理者・Supporter）だけが見られるプライベートスレッドを作成し、内容を投稿する。"""
    name = f"{member.display_name}さん｜{subject}"[:THREAD_NAME_MAX]

    thread = await channel.create_thread(
        name=name,
        type=discord.ChannelType.private_thread,
        invitable=False,  # 本人が他のメンバーを招待できないようにする
        auto_archive_duration=10080,  # 7日
        reason=f"お問い合わせ（{member} / {member.id}）",
    )
    await thread.add_user(member)

    notify_roles = [
        role
        for role in (
            channel.guild.get_role(role_id)
            for role_id in config.notify_role_ids
        )
        if role is not None
    ]

    embed = discord.Embed(
        title=subject,
        description=body,
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.set_author(
        name=f"{member.display_name}（{member.id}）",
        icon_url=member.display_avatar.url,
    )

    await thread.send(
        content=(
            " ".join(role.mention for role in notify_roles)
            + f"\n{member.mention} さんからお問い合わせがありました。"
        ),
        embed=embed,
        allowed_mentions=discord.AllowedMentions(
            everyone=False,
            users=[member],
            roles=notify_roles,
        ),
    )
    await thread.send(rainbowl_texts.INQUIRY_THREAD_GUIDE_TEXT)

    return thread


class InquiryOpenButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="問い合わせる",
            emoji="📮",
            style=discord.ButtonStyle.primary,
            custom_id=INQUIRY_OPEN_CUSTOM_ID,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        config = await get_inquiry_config(interaction.guild_id)

        if config is None:
            await interaction.response.send_message(
                "現在お問い合わせを受け付けていません。",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(InquiryModal(config))


class InquiryPanelView(discord.ui.View):
    """「問い合わせる」ボタンの永続View。"""

    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(InquiryOpenButton())


class RainbowlInquiry(commands.Cog):
    """rainbowl：お問い合わせ窓口"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="set_inquiry_button",
        description="お問い合わせボタンを再設置します（既存のBotメッセージは削除されます・管理者専用）",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def set_inquiry_button(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)

        config = await get_inquiry_config(interaction.guild_id)

        if config is None:
            await interaction.followup.send(
                "このサーバーにはお問い合わせ窓口（inquiry）が設定されていません。",
                ephemeral=True,
            )
            return

        channel = interaction.guild.get_channel(config.channel_id)

        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send(
                "お問い合わせチャンネルが見つかりませんでした。",
                ephemeral=True,
            )
            return

        try:
            await channel.purge(
                limit=50,
                check=lambda m: m.author.id == interaction.client.user.id,
            )
        except discord.HTTPException as exc:
            print(
                "[inquiry] 既存メッセージの削除に失敗しました"
                f" channel_id={channel.id} error={exc}"
            )

        await channel.send(
            embed=discord.Embed(
                title=rainbowl_texts.INQUIRY_PANEL_TITLE,
                description=rainbowl_texts.INQUIRY_PANEL_DESCRIPTION,
                color=discord.Color.blurple(),
            ),
            view=InquiryPanelView(),
        )

        await interaction.followup.send(
            f"お問い合わせボタンを設置しました → {channel.mention}",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RainbowlInquiry(bot))

    # 永続View：Bot起動のたびに再登録する
    bot.add_view(InquiryPanelView())
