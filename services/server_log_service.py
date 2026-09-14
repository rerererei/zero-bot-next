# services/server_log_service.py
"""
全ギルド共通のサーバーログ機能（管理ログ・編集ログ・削除ログ・VCログ）。

Rainbowl専用ではなく、guild_config["server_logs"]に投稿先チャンネルが
設定されているギルドでのみ、対応する種別のログを送信する
（設定が無ければ何もしない＝未設定ギルドには一切影響しない）。

既存のGuildConfigStore（zero_bot_guild_config）を使い回す。
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional

import discord

from data.guild_config_store import GuildConfigStore

JST = timezone(timedelta(hours=9))
WEEKDAY_JP = ["月", "火", "水", "木", "金", "土", "日"]

LOG_TYPE_ADMIN = "admin_log_channel_id"
LOG_TYPE_EDIT = "edit_log_channel_id"
LOG_TYPE_DELETE = "delete_log_channel_id"
LOG_TYPE_VOICE = "voice_log_channel_id"

LOG_TYPE_CHOICES = [
    ("管理ログ（ロール変更）", LOG_TYPE_ADMIN),
    ("編集ログ", LOG_TYPE_EDIT),
    ("削除ログ", LOG_TYPE_DELETE),
    ("VCログ", LOG_TYPE_VOICE),
]

# 編集前・編集後・削除本文、それぞれこの文字数で省略する
TRUNCATE_LENGTH = 400

COLOR_ADMIN = 0xEB459E
COLOR_EDIT = 0x5865F2
COLOR_DELETE = 0x992D22
COLOR_VOICE_JOIN = 0x2ECC71
COLOR_VOICE_LEAVE = 0xE74C3C

CONTENT_REMOVED_TEXT = "（コンテンツが削除されました）"

guild_config_store = GuildConfigStore()

# ログ送信対象とみなすチャンネル種別（VCインチャ・スレッドを含む）
LOGGABLE_CHANNEL_TYPES = (
    discord.TextChannel,
    discord.VoiceChannel,
    discord.Thread,
)


def _load_full_config(guild_id: int) -> Dict[str, Any]:
    return guild_config_store.get_config(guild_id) or {}


def get_server_log_settings(guild_id: int) -> Dict[str, Any]:
    cfg = _load_full_config(guild_id)
    settings = cfg.get("server_logs")
    return settings if isinstance(settings, dict) else {}


async def set_log_channel(
    guild_id: int, log_type: str, channel_id: int
) -> None:
    def _apply():
        cfg = _load_full_config(guild_id)
        settings = cfg.get("server_logs")
        settings = dict(settings) if isinstance(settings, dict) else {}
        settings[log_type] = str(channel_id)
        cfg = {**cfg, "server_logs": settings}
        guild_config_store.save_config(guild_id, cfg)

    await asyncio.to_thread(_apply)


def _get_log_channel(
    guild: discord.Guild, log_type: str
) -> Optional[discord.TextChannel]:
    settings = get_server_log_settings(guild.id)
    raw_id = settings.get(log_type)
    if not raw_id:
        return None

    channel = guild.get_channel(int(raw_id))
    return channel if isinstance(channel, discord.TextChannel) else None


def truncate(text: Optional[str], limit: int = TRUNCATE_LENGTH) -> str:
    text = text or ""
    if len(text) > limit:
        return text[:limit].rstrip() + "…"
    return text


def format_footer(guild: discord.Guild, dt: datetime) -> str:
    dt_jst = dt.astimezone(JST)
    weekday = WEEKDAY_JP[dt_jst.weekday()]
    return (
        f"{guild.name}・{dt_jst.year}/{dt_jst.month:02d}/"
        f"{dt_jst.day:02d}({weekday}) {dt_jst.hour}:{dt_jst.minute:02d}"
    )


async def _send(channel: discord.TextChannel, embed: discord.Embed) -> None:
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        pass


# =============================
#    管理ログ（ロールの付与・剥奪）
# =============================
async def send_role_change_log(
    member: discord.Member,
    added: Iterable[discord.Role],
    removed: Iterable[discord.Role],
) -> None:
    channel = _get_log_channel(member.guild, LOG_TYPE_ADMIN)
    if channel is None:
        return

    lines = [f"✅ {role.name}" for role in added]
    lines += [f"⛔ {role.name}" for role in removed]
    if not lines:
        return

    embed = discord.Embed(
        description=(
            f"📝 {member.mention} が更新されました。\n\n"
            "ロール:\n" + "\n".join(lines)
        ),
        color=COLOR_ADMIN,
    )
    embed.set_author(
        name=str(member), icon_url=member.display_avatar.url
    )
    embed.set_footer(
        text=format_footer(member.guild, datetime.now(timezone.utc))
    )
    await _send(channel, embed)


# =============================
#    編集ログ
# =============================
async def send_message_edit_log(
    before: discord.Message, after: discord.Message
) -> None:
    channel = _get_log_channel(after.guild, LOG_TYPE_EDIT)
    if channel is None:
        return

    embed = discord.Embed(
        description=(
            f"📝 {after.author.mention} が {after.channel.mention} で"
            f"送信したメッセージが編集されました。\n"
            f"[ページへ移動]({after.jump_url})"
        ),
        color=COLOR_EDIT,
    )
    embed.add_field(
        name="変更前",
        value=truncate(before.content) or "（空欄）",
        inline=False,
    )
    embed.add_field(
        name="変更後",
        value=truncate(after.content) or "（空欄）",
        inline=False,
    )
    embed.set_footer(
        text=format_footer(after.guild, datetime.now(timezone.utc))
    )
    await _send(channel, embed)


# =============================
#    削除ログ
# =============================
async def send_message_delete_log(message: discord.Message) -> None:
    channel = _get_log_channel(message.guild, LOG_TYPE_DELETE)
    if channel is None:
        return

    content = (message.content or "").strip()
    content_display = truncate(content) if content else CONTENT_REMOVED_TEXT

    embed = discord.Embed(
        description=(
            f"🗑️ {message.author.mention} が送信したメッセージが "
            f"{message.channel.mention} で削除されました。\n\n"
            f"{content_display}"
        ),
        color=COLOR_DELETE,
    )
    embed.set_footer(
        text=format_footer(message.guild, datetime.now(timezone.utc))
    )
    await _send(channel, embed)


# =============================
#    VCログ（入退室）
# =============================
async def send_voice_state_log(
    member: discord.Member,
    channel_obj: discord.VoiceChannel,
    *,
    joined: bool,
) -> None:
    log_channel = _get_log_channel(member.guild, LOG_TYPE_VOICE)
    if log_channel is None:
        return

    verb = "参加しました" if joined else "退出しました"
    color = COLOR_VOICE_JOIN if joined else COLOR_VOICE_LEAVE

    embed = discord.Embed(
        description=(
            f"{member.mention} がボイスチャンネルに{verb} "
            f"{channel_obj.mention}."
        ),
        color=color,
    )
    embed.set_footer(
        text=format_footer(member.guild, datetime.now(timezone.utc))
    )
    await _send(log_channel, embed)
