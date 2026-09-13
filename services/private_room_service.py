# services/private_room_service.py
"""
rainbowl専用プライベートルーム機能の状態遷移ロジック本体。

Cog（cogs/rainbowl_private_rooms.py）はDiscordイベント・インタラクションの
受け口として薄く保ち、判定・DynamoDB操作・チャンネル操作はこのモジュールに
集約する（rainbowl_onboarding_serviceと同じ方針）。

このモジュールが対応するのは「プライベート会議」「プライベート個室」の
2種別のみ（プライベートルーム機能.md 1章＋個室は会議の1対1版として追加）。
権限モデル・削除・自動修復等の挙動は両者で共通で、違いは作成フロー
（人数入力か、相手を1人選ぶか）だけ。他の部屋種別（パブリック会議等）を
将来追加する場合でも、このモジュールを直接は再利用しない前提。

rainbowl専用機能であることのゲートは、既存rainbowl系Cogと同じく
guild_config["rainbowl"]の有無で判定する（ハードコードのギルドIDは使わない）。
設定は guild_config["rainbowl"]["private_room"] に隔離し、
RainbowlGuildConfig（rainbowl_config_service.py）には追加しない
（必須フィールド追加で入場処理ごと停止した過去の本番障害を踏まえた判断。
 docs/rainbowl/今後のTODO.md参照）。

簡略化した仕様（ユーザー合意済み）:
- 24章「Bot枠と人数上限」: human_limit/effective_limitを別々のDB項目として
  管理する完全版までは実装せず、「Botが接続している間だけ、その分だけ
  Discordのuser_limitを一時的に+1する」補正のみ入れている
  （compute_effective_user_limit / adjust_limit_for_bot_presence）。
  個室（human_limit=2固定）でBotが人間より先に入室すると、そのままでは
  招待した相手が入室できなくなる事故が起きるため。
- 33章「権限の自動修復」: services/private_room_permissions.py 参照。
  @everyone・作成者・招待ユーザー・Bot以外の個別メンバー上書きのみ削除対象とし、
  ロール上書きには一切触れない。
"""

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import discord

from data.guild_config_store import GuildConfigStore
from data.rainbowl.private_room_store import PrivateRoomStore
from services.private_room_permissions import (
    BOT_OVERWRITE,
    EVERYONE_OVERWRITE,
    MEMBER_OVERWRITE,
    apply_room_permissions,
)
from utils.room_text_validation import (
    RoomTextValidationError,
    validate_room_text,
)

JST = timezone(timedelta(hours=9))

ROOM_TYPE_PRIVATE_MEETING = "PRIVATE_MEETING"
ROOM_TYPE_PRIVATE_SOLO = "PRIVATE_SOLO"

ROOM_TYPE_CHOICES: List[Tuple[str, str]] = [
    ("プライベート会議", ROOM_TYPE_PRIVATE_MEETING),
    ("プライベート個室", ROOM_TYPE_PRIVATE_SOLO),
]

ROOM_TYPE_DISPLAY_NAMES = {
    ROOM_TYPE_PRIVATE_MEETING: "プライベート会議",
    ROOM_TYPE_PRIVATE_SOLO: "プライベート個室",
}

# プライベート個室は「作成者+相手1人」固定。人数変更メニューからは
# あとで自由に変更できる(会議と同じ仕様に合わせるため)。
SOLO_ROOM_HUMAN_LIMIT = 2

BITRATE_CHOICES: List[Tuple[str, int]] = [
    ("デフォルト(64kbps)", 64000),
    ("96kbps", 96000),
    ("128kbps", 128000),
    ("256kbps", 256000),
    ("384kbps", 384000),
]

# 作成時はビットレート選択を挟まず、この値で即作成する。
# 作成後の変更はルームメニューの「ビットレート変更」から引き続き可能。
DEFAULT_BITRATE_BPS = BITRATE_CHOICES[0][1]

EMPTY_AUTO_DELETE_SECONDS = 60

DELETE_RECREATE_COOLDOWN_SECONDS = 30.0
ROOM_MENU_COOLDOWN_SECONDS = 3.0
INVITE_COOLDOWN_SECONDS = 5.0

guild_config_store = GuildConfigStore()
store = PrivateRoomStore()

_room_locks: Dict[int, asyncio.Lock] = {}
_owner_create_locks: Dict[int, asyncio.Lock] = {}
_recreate_cooldown_until: Dict[int, float] = {}


def _mark_recreate_cooldown(owner_id: int) -> None:
    """ルーム削除後の再作成クールダウン（45章）の起点を記録する。"""
    _recreate_cooldown_until[owner_id] = (
        time.monotonic() + DELETE_RECREATE_COOLDOWN_SECONDS
    )


def _check_recreate_cooldown(owner_id: int) -> None:
    until = _recreate_cooldown_until.get(owner_id)
    if until is not None and time.monotonic() < until:
        raise PrivateRoomError(
            "ルーム削除後、しばらく時間をおいてから"
            "次のルームを作成してください。"
        )


class PrivateRoomError(Exception):
    """ユーザーへそのまま表示してよいエラー。"""


def _now_iso() -> str:
    return datetime.now(JST).isoformat()


def now_iso() -> str:
    return _now_iso()


def get_room_lock(channel_id: int) -> asyncio.Lock:
    lock = _room_locks.get(channel_id)
    if lock is None:
        lock = asyncio.Lock()
        _room_locks[channel_id] = lock
    return lock


def get_owner_create_lock(owner_id: int) -> asyncio.Lock:
    lock = _owner_create_locks.get(owner_id)
    if lock is None:
        lock = asyncio.Lock()
        _owner_create_locks[owner_id] = lock
    return lock


def human_member_count(channel: discord.VoiceChannel) -> int:
    return len([m for m in channel.members if not m.bot])


def compute_effective_user_limit(
    channel: discord.VoiceChannel, human_limit: int
) -> int:
    """
    人間用の人数上限(0=無制限)に、現在接続中のBotの数だけ上乗せした
    実際にDiscordへ設定すべきuser_limitを返す(24章の簡略版)。

    Botが人数枠を1つ食うことで、あとから来る人間が入室できなくなる
    事故を防ぐための一時的な補正。無制限設定の場合は補正しない。
    """
    if human_limit == 0:
        return 0

    bot_count = len([m for m in channel.members if m.bot])
    return min(human_limit + bot_count, 99)


# =============================
#    rainbowl専用ゲート・設定
# =============================
def is_rainbowl_guild(guild_id: int) -> bool:
    cfg = guild_config_store.get_config(guild_id) or {}
    return bool(cfg.get("rainbowl"))


def _load_full_config(guild_id: int) -> Dict[str, Any]:
    return guild_config_store.get_config(guild_id) or {}


def _save_private_room_settings(
    guild_id: int, settings: Dict[str, Any]
) -> None:
    cfg = _load_full_config(guild_id)
    rainbowl_cfg = cfg.get("rainbowl")
    if not isinstance(rainbowl_cfg, dict):
        rainbowl_cfg = {}
    rainbowl_cfg = {**rainbowl_cfg, "private_room": settings}
    cfg = {**cfg, "rainbowl": rainbowl_cfg}
    guild_config_store.save_config(guild_id, cfg)


def get_private_room_settings(guild_id: int) -> Dict[str, Any]:
    cfg = _load_full_config(guild_id)
    rainbowl_cfg = cfg.get("rainbowl")
    if not isinstance(rainbowl_cfg, dict):
        return {}
    settings = rainbowl_cfg.get("private_room")
    return settings if isinstance(settings, dict) else {}


async def set_log_channel(guild_id: int, channel_id: int) -> None:
    def _apply():
        settings = get_private_room_settings(guild_id)
        settings["log_channel_id"] = str(channel_id)
        _save_private_room_settings(guild_id, settings)

    await asyncio.to_thread(_apply)


def is_system_enabled(guild_id: int) -> bool:
    settings = get_private_room_settings(guild_id)
    return bool(settings.get("system_enabled", True))


async def set_system_enabled(guild_id: int, enabled: bool) -> None:
    def _apply():
        settings = get_private_room_settings(guild_id)
        settings["system_enabled"] = enabled
        _save_private_room_settings(guild_id, settings)

    await asyncio.to_thread(_apply)


async def get_menus(guild_id: int) -> List[Dict[str, Any]]:
    settings = await asyncio.to_thread(get_private_room_settings, guild_id)
    menus = settings.get("menus")
    return menus if isinstance(menus, list) else []


async def add_menu(
    guild_id: int,
    room_type: str,
    category_id: int,
    menu_channel_id: int,
    message_id: int,
) -> bool:
    """
    同一の 部屋種別+作成先カテゴリ の重複設置を拒否する（2章）。
    成功時True、重複によりFalseを返す。
    """

    def _apply() -> bool:
        settings = get_private_room_settings(guild_id)
        menus = settings.get("menus")
        menus = list(menus) if isinstance(menus, list) else []

        for menu in menus:
            if (
                menu.get("room_type") == room_type
                and str(menu.get("category_id")) == str(category_id)
            ):
                return False

        menus.append(
            {
                "room_type": room_type,
                "category_id": str(category_id),
                "menu_channel_id": str(menu_channel_id),
                "message_id": str(message_id),
            }
        )
        settings["menus"] = menus
        _save_private_room_settings(guild_id, settings)
        return True

    return await asyncio.to_thread(_apply)


async def remove_menu(
    guild_id: int, room_type: str, category_id: int
) -> bool:
    def _apply() -> bool:
        settings = get_private_room_settings(guild_id)
        menus = settings.get("menus")
        menus = list(menus) if isinstance(menus, list) else []

        new_menus = [
            menu
            for menu in menus
            if not (
                menu.get("room_type") == room_type
                and str(menu.get("category_id")) == str(category_id)
            )
        ]
        if len(new_menus) == len(menus):
            return False

        settings["menus"] = new_menus
        _save_private_room_settings(guild_id, settings)
        return True

    return await asyncio.to_thread(_apply)


async def mark_menu_category_missing(
    guild_id: int, category_id: int
) -> None:
    def _apply():
        settings = get_private_room_settings(guild_id)
        menus = settings.get("menus")
        menus = list(menus) if isinstance(menus, list) else []
        for menu in menus:
            if str(menu.get("category_id")) == str(category_id):
                menu["category_missing"] = True
        settings["menus"] = menus
        _save_private_room_settings(guild_id, settings)

    await asyncio.to_thread(_apply)


# =============================
#    入力検証・派生値
# =============================
def default_room_name(member: discord.Member) -> str:
    name = f"{member.display_name}'s ROOM"
    return name[:80]


def parse_human_limit(raw: str) -> Optional[int]:
    """戻り値: None = 無制限。不正な場合はPrivateRoomError。"""
    raw = raw.strip()

    if raw == "":
        return None

    if not raw.isascii() or not raw.isdigit():
        raise PrivateRoomError("人数は半角数字で入力してください。")

    value = int(raw)

    if value in (1, 2):
        raise PrivateRoomError(
            "1〜2人での利用は個室機能をご利用ください。"
            "プライベート会議は3人以上を想定しています。"
        )

    if value < 3 or value > 99:
        raise PrivateRoomError("人数は3〜99人の範囲で指定してください。")

    return value


def resolve_bitrate(
    requested_bps: int, guild: discord.Guild
) -> Tuple[int, bool]:
    """
    サーバーで設定可能な最大ビットレートへ自動補正する（4章）。
    戻り値: (実際に使う値, 補正が発生したか)
    """
    max_bitrate = int(guild.bitrate_limit)
    if requested_bps <= max_bitrate:
        return requested_bps, False
    return max_bitrate, True


def category_is_private_safe(category: discord.CategoryChannel) -> bool:
    """
    プライベート性を保証できないカテゴリかどうかを判定する（7章）。
    @everyone、または管理者権限を持たない他ロールへ「チャンネルを見る」を
    許可する上書きが存在する場合は「保証できない」と判定する。
    """
    guild = category.guild

    everyone_ow = category.overwrites_for(guild.default_role)
    if everyone_ow.view_channel is True:
        return False

    for target, overwrite in category.overwrites.items():
        if not isinstance(target, discord.Role):
            continue
        if target.id == guild.default_role.id:
            continue
        if target.permissions.administrator:
            continue
        if overwrite.view_channel is True:
            return False

    return True


def build_room_menu_embed(
    owner: discord.Member,
) -> discord.Embed:
    embed = discord.Embed(
        title="🔒 プライベートルーム メニュー",
        description=(
            "このルームの設定は、以下のボタンから操作できます。\n"
            "操作できるのは作成者・招待ユーザー・管理者のみです。"
        ),
        color=discord.Color.dark_purple(),
    )
    embed.add_field(
        name="作成者", value=owner.mention, inline=False
    )
    return embed


def is_operator(member: discord.Member, room: Dict[str, Any]) -> bool:
    if member.guild_permissions.administrator:
        return True
    if str(member.id) == str(room.get("owner_id")):
        return True
    if str(member.id) in (room.get("invited_user_ids") or []):
        return True
    return False


def _ensure_operator(member: discord.Member, room: Dict[str, Any]) -> None:
    if not is_operator(member, room):
        raise PrivateRoomError(
            "このルームの作成者・招待ユーザー・管理者のみ操作できます。"
        )


async def _ensure_system_enabled(guild_id: int) -> None:
    if not await asyncio.to_thread(is_system_enabled, guild_id):
        raise PrivateRoomError(
            "現在、ルーム作成機能はメンテナンスのため停止しています。"
        )


async def get_room_for_channel(
    guild_id: int, channel_id: int
) -> Dict[str, Any]:
    room = await asyncio.to_thread(store.get_room, guild_id, channel_id)
    if room is None:
        raise PrivateRoomError(
            "このチャンネルはZeroBot管理下のプライベートルームではありません。"
        )
    return room


# =============================
#    操作ログ
# =============================
def _build_log_embed(
    title: str, description: str, fields: Dict[str, Any]
) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        color=discord.Color.blurple(),
        timestamp=datetime.now(timezone.utc),
    )
    for name, value in fields.items():
        embed.add_field(name=name, value=str(value), inline=False)
    return embed


async def log_event(
    guild: discord.Guild,
    title: str,
    description: str,
    **fields: Any,
) -> None:
    embed = _build_log_embed(title, description, fields)

    settings = await asyncio.to_thread(
        get_private_room_settings, guild.id
    )
    log_channel_id = settings.get("log_channel_id")
    channel = (
        guild.get_channel(int(log_channel_id))
        if log_channel_id
        else None
    )

    if isinstance(channel, discord.TextChannel):
        try:
            await channel.send(embed=embed)
            return
        except discord.HTTPException:
            pass

    log_id = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    await asyncio.to_thread(
        store.enqueue_log, guild.id, log_id, embed.to_dict()
    )


async def flush_log_queue(guild: discord.Guild) -> None:
    settings = await asyncio.to_thread(
        get_private_room_settings, guild.id
    )
    log_channel_id = settings.get("log_channel_id")
    if not log_channel_id:
        return

    channel = guild.get_channel(int(log_channel_id))
    if not isinstance(channel, discord.TextChannel):
        return

    queued = await asyncio.to_thread(store.list_queued_logs, guild.id)
    for item in queued:
        log_id = item["sort_key"].split("#", 1)[1]
        try:
            embed = discord.Embed.from_dict(item["payload"])
            await channel.send(embed=embed)
            await asyncio.to_thread(
                store.delete_queued_log, guild.id, log_id
            )
        except discord.HTTPException:
            break


def has_log_channel_configured(guild_id: int) -> bool:
    settings = get_private_room_settings(guild_id)
    return bool(settings.get("log_channel_id"))


# =============================
#    ルーム作成
# =============================
async def create_room(
    guild: discord.Guild,
    member: discord.Member,
    category_id: int,
    room_name_raw: Optional[str],
    human_limit: Optional[int],
    requested_bitrate: int,
    room_type: str = ROOM_TYPE_PRIVATE_MEETING,
    initial_invited_ids: Optional[List[int]] = None,
) -> Tuple[discord.VoiceChannel, bool, int]:
    """
    プライベートルーム（会議／個室）を作成する。

    initial_invited_idsを渡すと、作成と同時にそのユーザーを招待済みとして
    登録する（個室で相手を選んでもらう用途）。無効なユーザー（Bot・
    作成者自身・サーバー未所属）は無条件で除外する。

    戻り値: (作成したVC, ビットレート補正が発生したか, 実際のビットレート)
    失敗時はPrivateRoomErrorを投げる。
    """
    if member.bot:
        raise PrivateRoomError("Botユーザーはこの機能を利用できません。")

    _check_recreate_cooldown(member.id)
    await _ensure_system_enabled(guild.id)

    if not await asyncio.to_thread(
        has_log_channel_configured, guild.id
    ):
        raise PrivateRoomError(
            "操作ログの設定に問題があるため、"
            "現在ルームを作成できません。管理者へお問い合わせください。"
        )

    category = guild.get_channel(category_id)
    if not isinstance(category, discord.CategoryChannel):
        raise PrivateRoomError(
            "作成先カテゴリが見つかりませんでした。管理者へお問い合わせください。"
        )

    if room_name_raw and room_name_raw.strip():
        try:
            room_name = validate_room_text(room_name_raw)
        except RoomTextValidationError as exc:
            raise PrivateRoomError(str(exc)) from exc
    else:
        room_name = default_room_name(member)

    now = _now_iso()

    lock = get_owner_create_lock(member.id)
    async with lock:
        existing = await asyncio.to_thread(
            store.get_room_by_owner, guild.id, member.id
        )
        if existing:
            raise PrivateRoomError(
                "既にルームを所有しているため、新しいルームを作成できません。\n"
                "所有しているルームが削除されるまで、次のルームは作成できません。"
            )

        actual_bitrate, corrected = resolve_bitrate(
            requested_bitrate, guild
        )

        initial_invitees: List[discord.Member] = []
        for raw_uid in initial_invited_ids or []:
            resolved = guild.get_member(raw_uid)
            if resolved is None or resolved.bot or resolved.id == member.id:
                continue
            if resolved.id not in {m.id for m in initial_invitees}:
                initial_invitees.append(resolved)

        overwrites = {
            guild.default_role: EVERYONE_OVERWRITE,
            guild.me: BOT_OVERWRITE,
            member: MEMBER_OVERWRITE,
        }
        for invitee in initial_invitees:
            overwrites[invitee] = MEMBER_OVERWRITE

        channel: Optional[discord.VoiceChannel] = None
        db_created = False

        try:
            channel = await guild.create_voice_channel(
                name=room_name,
                category=category,
                user_limit=human_limit or 0,
                bitrate=actual_bitrate,
                overwrites=overwrites,
                reason=f"プライベートルーム作成: {member} ({member.id})",
            )

            db_created = await asyncio.to_thread(
                store.create_room,
                guild.id,
                member.id,
                channel.id,
                room_type,
                category.id,
                room_name,
                human_limit,
                actual_bitrate,
                now,
                [str(invitee.id) for invitee in initial_invitees],
            )

            if not db_created:
                raise PrivateRoomError(
                    "既にルームを所有しているため、"
                    "新しいルームを作成できません。"
                )

            # 遅延importで循環importを避ける（Viewはcogs側で定義）
            from cogs.rainbowl_private_rooms import RoomMenuView

            menu_message = await channel.send(
                content=f"{member.mention} さんのプライベートルームです。",
                embed=build_room_menu_embed(member),
                view=RoomMenuView(),
            )

            if initial_invitees:
                mentions = " ".join(u.mention for u in initial_invitees)
                await channel.send(
                    f"{mentions}\n\n"
                    "🔒 このプライベートルームに招待されました。\n"
                    "VCへの参加とルームメニューの操作が可能です。"
                )

            await asyncio.to_thread(
                store.update_room_menu_message_id,
                guild.id,
                channel.id,
                menu_message.id,
                now,
            )
            await asyncio.to_thread(
                store.set_state, guild.id, channel.id, "ACTIVE", now
            )
            await asyncio.to_thread(
                store.set_empty_since, guild.id, channel.id, now, now
            )

        except Exception as exc:
            if channel is not None:
                try:
                    await channel.delete(
                        reason="プライベートルーム作成失敗によるロールバック"
                    )
                except discord.HTTPException:
                    pass

            if db_created and channel is not None:
                try:
                    await asyncio.to_thread(
                        store.delete_room,
                        guild.id,
                        member.id,
                        channel.id,
                    )
                except Exception:
                    await asyncio.to_thread(
                        store.set_state,
                        guild.id,
                        channel.id,
                        "CLEANUP_REQUIRED",
                        now,
                    )

            await log_event(
                guild,
                "⚠️ ルーム作成失敗",
                "ルーム作成中にエラーが発生し、ロールバックしました。",
                作成者=f"{member} ({member.id})",
                エラー=str(exc),
            )

            if isinstance(exc, PrivateRoomError):
                raise
            raise PrivateRoomError(
                "ルームの作成に失敗しました。時間をおいて再度お試しください。"
            ) from exc

        await log_event(
            guild,
            "🔒 ルーム作成",
            "プライベートルームが作成されました。",
            種別=ROOM_TYPE_DISPLAY_NAMES.get(room_type, room_type),
            作成者=f"{member} ({member.id})",
            チャンネル=channel.mention,
            部屋名=room_name,
            人数上限="無制限" if human_limit is None else human_limit,
            ビットレート=f"{actual_bitrate}bps"
            + ("（自動補正）" if corrected else ""),
            招待済み=(
                ", ".join(f"{u} ({u.id})" for u in initial_invitees)
                if initial_invitees
                else "なし"
            ),
        )

        return channel, corrected, actual_bitrate


# =============================
#    ルーム削除
# =============================
async def _perform_delete(
    guild: discord.Guild,
    room: Dict[str, Any],
    *,
    reason: str,
    log_title: str,
) -> None:
    channel_id = int(room["channel_id"])
    owner_id = int(room["owner_id"])
    channel = guild.get_channel(channel_id)

    if channel is not None:
        try:
            await channel.delete(reason=reason)
        except discord.HTTPException:
            pass

    await asyncio.to_thread(
        store.delete_room, guild.id, owner_id, channel_id
    )
    _mark_recreate_cooldown(owner_id)

    owner = guild.get_member(owner_id)
    await log_event(
        guild,
        log_title,
        reason,
        作成者=f"{owner} ({owner_id})" if owner else str(owner_id),
        チャンネル名=room.get("room_name", "?"),
    )


async def request_manual_delete(
    guild: discord.Guild, member: discord.Member
) -> None:
    room = await asyncio.to_thread(
        store.get_room_by_owner, guild.id, member.id
    )
    if not room:
        raise PrivateRoomError(
            "あなたが作成者になっているルームが見つかりませんでした。"
        )

    channel_id = int(room["channel_id"])
    channel = guild.get_channel(channel_id)

    if channel is None:
        await asyncio.to_thread(
            store.delete_room, guild.id, member.id, channel_id
        )
        raise PrivateRoomError("ルームは既に削除されています。")

    lock = get_room_lock(channel.id)
    async with lock:
        if human_member_count(channel) > 0:
            raise PrivateRoomError(
                "現在、ルーム内に利用中のユーザーがいるため削除できません。\n\n"
                "ルームに戻るか、インチャで声をかけるなどして、"
                "全員が退出してからもう一度操作してください。"
            )

        # 削除直前の再確認（27章）
        fresh_channel = guild.get_channel(channel.id)
        if fresh_channel is None:
            await asyncio.to_thread(
                store.delete_room, guild.id, member.id, channel_id
            )
            raise PrivateRoomError("ルームは既に削除されています。")

        if human_member_count(fresh_channel) > 0:
            raise PrivateRoomError(
                "削除直前に誰かが入室したため、削除を中止しました。"
            )

        await _perform_delete(
            guild,
            room,
            reason=f"{member} ({member.id}) による手動削除",
            log_title="🗑️ ルーム手動削除",
        )


async def auto_delete_empty_rooms(guild: discord.Guild) -> None:
    """無人状態が1分継続したルームを自動削除する（29〜31章・35章）。"""
    rooms = await asyncio.to_thread(store.list_active_rooms, guild.id)
    now = datetime.now(JST)

    for room in rooms:
        if room.get("state") not in ("ACTIVE", "EMPTY_WAIT"):
            continue

        channel_id = int(room["channel_id"])
        channel = guild.get_channel(channel_id)

        if channel is None:
            # Bot停止中に外部から削除されていた等（37章オフライン整合性）
            await asyncio.to_thread(
                store.delete_room,
                guild.id,
                int(room["owner_id"]),
                channel_id,
            )
            continue

        lock = get_room_lock(channel_id)
        async with lock:
            fresh = await asyncio.to_thread(
                store.get_room, guild.id, channel_id
            )
            if not fresh or fresh.get("state") not in (
                "ACTIVE",
                "EMPTY_WAIT",
            ):
                continue

            empty_since_raw = fresh.get("empty_since")
            if not empty_since_raw:
                continue

            elapsed = (
                now - datetime.fromisoformat(empty_since_raw)
            ).total_seconds()
            if elapsed < EMPTY_AUTO_DELETE_SECONDS:
                continue

            if human_member_count(channel) > 0:
                continue

            await _perform_delete(
                guild,
                fresh,
                reason="無人状態が1分継続したため自動削除",
                log_title="🗑️ ルーム自動削除",
            )


async def on_channel_became_empty(
    guild: discord.Guild, channel: discord.VoiceChannel
) -> None:
    now = _now_iso()
    room = await asyncio.to_thread(store.get_room, guild.id, channel.id)
    if not room or room.get("state") != "ACTIVE":
        return

    await asyncio.to_thread(
        store.set_empty_since, guild.id, channel.id, now, now
    )
    await asyncio.to_thread(
        store.set_state, guild.id, channel.id, "EMPTY_WAIT", now
    )


async def on_channel_became_occupied(
    guild: discord.Guild, channel: discord.VoiceChannel
) -> None:
    now = _now_iso()
    room = await asyncio.to_thread(store.get_room, guild.id, channel.id)
    if not room or room.get("state") != "EMPTY_WAIT":
        return

    await asyncio.to_thread(
        store.set_empty_since, guild.id, channel.id, None, now
    )
    await asyncio.to_thread(
        store.set_state, guild.id, channel.id, "ACTIVE", now
    )


async def adjust_limit_for_bot_presence(
    guild: discord.Guild, channel: discord.VoiceChannel
) -> None:
    """
    Botの入退室で人間の人数枠が奪われないよう、人数上限を一時的に
    補正する（24章）。人間用のhuman_limit自体は変更しない。
    """
    room = await asyncio.to_thread(store.get_room, guild.id, channel.id)
    if not room:
        return

    human_limit = room.get("human_limit") or 0
    if human_limit == 0:
        return  # 無制限は補正不要

    effective_limit = compute_effective_user_limit(channel, human_limit)
    if channel.user_limit == effective_limit:
        return

    try:
        await channel.edit(
            user_limit=effective_limit,
            reason="Bot枠による人数上限の一時補正",
        )
    except discord.HTTPException:
        return

    await log_event(
        guild,
        "🤖 Bot枠による人数補正",
        "Botの入退室に合わせて人数上限を一時的に補正しました。",
        人間用人数上限="無制限" if human_limit == 0 else human_limit,
        Discord実上限=effective_limit,
    )


# =============================
#    部屋名・ステータス・人数・ビットレート変更
# =============================
async def rename_room(
    guild: discord.Guild,
    member: discord.Member,
    channel: discord.VoiceChannel,
    new_name_raw: str,
) -> str:
    await _ensure_system_enabled(guild.id)
    room = await get_room_for_channel(guild.id, channel.id)
    _ensure_operator(member, room)

    if not new_name_raw or not new_name_raw.strip():
        raise PrivateRoomError("部屋名を空欄にすることはできません。")

    try:
        new_name = validate_room_text(new_name_raw)
    except RoomTextValidationError as exc:
        raise PrivateRoomError(str(exc)) from exc

    now = _now_iso()
    async with get_room_lock(channel.id):
        old_name = room.get("room_name", "")
        await channel.edit(
            name=new_name, reason=f"{member} による部屋名変更"
        )
        await asyncio.to_thread(
            store.update_room_name, guild.id, channel.id, new_name, now
        )

    await log_event(
        guild,
        "✏️ 部屋名変更",
        "部屋名が変更されました。",
        操作ユーザー=f"{member} ({member.id})",
        変更前=old_name,
        変更後=new_name,
    )
    return new_name


async def set_status(
    guild: discord.Guild,
    member: discord.Member,
    channel: discord.VoiceChannel,
    status_raw: str,
) -> Optional[str]:
    await _ensure_system_enabled(guild.id)
    room = await get_room_for_channel(guild.id, channel.id)
    _ensure_operator(member, room)

    now = _now_iso()

    if not status_raw or not status_raw.strip():
        async with get_room_lock(channel.id):
            try:
                await channel.edit(status=None)
            except (discord.HTTPException, TypeError):
                pass
            await asyncio.to_thread(
                store.update_status_text, guild.id, channel.id, None, now
            )
        await log_event(
            guild,
            "📋 ステータス解除",
            "ステータスが解除されました。",
            操作ユーザー=f"{member} ({member.id})",
        )
        return None

    try:
        new_status = validate_room_text(status_raw)
    except RoomTextValidationError as exc:
        raise PrivateRoomError(str(exc)) from exc

    async with get_room_lock(channel.id):
        try:
            await channel.edit(status=new_status)
        except (discord.HTTPException, TypeError):
            pass
        await asyncio.to_thread(
            store.update_status_text,
            guild.id,
            channel.id,
            new_status,
            now,
        )

    await log_event(
        guild,
        "📋 ステータス変更",
        "ステータスが変更されました。",
        操作ユーザー=f"{member} ({member.id})",
        ステータス=new_status,
    )
    return new_status


async def change_limit(
    guild: discord.Guild,
    member: discord.Member,
    channel: discord.VoiceChannel,
    raw_limit: str,
) -> Optional[int]:
    await _ensure_system_enabled(guild.id)
    room = await get_room_for_channel(guild.id, channel.id)
    _ensure_operator(member, room)

    new_limit = parse_human_limit(raw_limit)
    now = _now_iso()

    async with get_room_lock(channel.id):
        await channel.edit(
            user_limit=compute_effective_user_limit(
                channel, new_limit or 0
            ),
            reason=f"{member} による人数変更",
        )
        await asyncio.to_thread(
            store.update_human_limit,
            guild.id,
            channel.id,
            new_limit,
            now,
        )

    await log_event(
        guild,
        "👥 人数変更",
        "人数上限が変更されました。",
        操作ユーザー=f"{member} ({member.id})",
        変更後="無制限" if new_limit is None else new_limit,
    )
    return new_limit


async def change_bitrate(
    guild: discord.Guild,
    member: discord.Member,
    channel: discord.VoiceChannel,
    requested_bps: int,
) -> Tuple[int, bool]:
    await _ensure_system_enabled(guild.id)
    room = await get_room_for_channel(guild.id, channel.id)
    _ensure_operator(member, room)

    actual_bitrate, corrected = resolve_bitrate(requested_bps, guild)
    now = _now_iso()

    async with get_room_lock(channel.id):
        await channel.edit(
            bitrate=actual_bitrate,
            reason=f"{member} によるビットレート変更",
        )
        await asyncio.to_thread(
            store.update_bitrate,
            guild.id,
            channel.id,
            actual_bitrate,
            now,
        )

    await log_event(
        guild,
        "🎚️ ビットレート変更"
        + ("（自動補正）" if corrected else ""),
        "ビットレートが変更されました。",
        操作ユーザー=f"{member} ({member.id})",
        変更後=f"{actual_bitrate}bps",
    )
    return actual_bitrate, corrected


# =============================
#    招待・招待解除
# =============================
async def invite_users(
    guild: discord.Guild,
    member: discord.Member,
    channel: discord.VoiceChannel,
    selected: List[discord.abc.Snowflake],
) -> List[discord.Member]:
    await _ensure_system_enabled(guild.id)
    room = await get_room_for_channel(guild.id, channel.id)
    _ensure_operator(member, room)

    owner_id = int(room["owner_id"])
    now = _now_iso()

    async with get_room_lock(channel.id):
        room = await get_room_for_channel(guild.id, channel.id)
        invited_ids = set(room.get("invited_user_ids") or [])

        newly_invited: List[discord.Member] = []
        for user in selected:
            resolved = guild.get_member(user.id)
            if resolved is None:
                continue  # サーバー退出済み等の無効ユーザー
            if resolved.bot:
                continue
            if resolved.id == owner_id:
                continue
            if str(resolved.id) in invited_ids:
                continue
            newly_invited.append(resolved)

        if not newly_invited:
            raise PrivateRoomError(
                "招待できる新しいユーザーがいませんでした。"
            )

        for user in newly_invited:
            room = await asyncio.to_thread(
                store.add_invited_user,
                guild.id,
                channel.id,
                user.id,
                now,
            )

        invited_ids_after = room.get("invited_user_ids") or []

        # 人数自動拡張（16章）：招待操作時のみ判定する
        required = 1 + len(invited_ids_after)
        current_limit = room.get("human_limit") or 0  # 0 = 無制限

        new_limit = current_limit
        if current_limit != 0 and required > current_limit:
            new_limit = 0 if required > 99 else required
            await asyncio.to_thread(
                store.update_human_limit,
                guild.id,
                channel.id,
                new_limit,
                now,
            )

        await apply_room_permissions(
            channel,
            guild,
            owner_id,
            invited_ids_after,
            guild.me,
            reason=f"{member} によるユーザー招待",
        )
        await channel.edit(
            user_limit=compute_effective_user_limit(
                channel, new_limit or 0
            )
        )

        mentions = " ".join(u.mention for u in newly_invited)
        await channel.send(
            f"{mentions}\n\n"
            "🔒 このプライベートルームに招待されました。\n"
            "VCへの参加とルームメニューの操作が可能です。"
        )

    await log_event(
        guild,
        "📨 ユーザー招待",
        "ユーザーが招待されました。",
        操作ユーザー=f"{member} ({member.id})",
        招待先=", ".join(f"{u} ({u.id})" for u in newly_invited),
    )
    return newly_invited


async def uninvite_user(
    guild: discord.Guild,
    member: discord.Member,
    channel: discord.VoiceChannel,
    target_id: int,
) -> None:
    room = await get_room_for_channel(guild.id, channel.id)

    is_self = target_id == member.id
    if not is_self:
        await _ensure_system_enabled(guild.id)
        _ensure_operator(member, room)

    owner_id = int(room["owner_id"])
    if target_id == owner_id:
        raise PrivateRoomError(
            "作成者を招待解除の対象にすることはできません。"
        )

    now = _now_iso()

    async with get_room_lock(channel.id):
        room = await get_room_for_channel(guild.id, channel.id)
        invited_ids = room.get("invited_user_ids") or []
        if str(target_id) not in invited_ids:
            raise PrivateRoomError("招待されていないユーザーです。")

        room = await asyncio.to_thread(
            store.remove_invited_user,
            guild.id,
            channel.id,
            target_id,
            now,
        )

        still_connected = any(
            m.id == target_id for m in channel.members
        )

        if still_connected:
            # VC接続中は強制切断しない（21章）。本人が退出したタイミングで
            # on_voice_state_updateから権限を剥がす。
            await asyncio.to_thread(
                store.add_pending_removal,
                guild.id,
                channel.id,
                target_id,
                now,
            )
        else:
            await apply_room_permissions(
                channel,
                guild,
                owner_id,
                room.get("invited_user_ids") or [],
                guild.me,
                reason=f"{member} による招待解除",
            )

    target_member = guild.get_member(target_id)
    await log_event(
        guild,
        "📤 招待解除",
        "招待ユーザーが招待解除されました。",
        操作ユーザー=f"{member} ({member.id})",
        対象ユーザー=(
            f"{target_member} ({target_id})"
            if target_member
            else str(target_id)
        ),
        本人による解除="はい" if is_self else "いいえ",
        VC接続中="はい" if still_connected else "いいえ",
    )


async def finalize_pending_removal(
    guild: discord.Guild,
    channel: discord.VoiceChannel,
    user_id: int,
) -> None:
    """招待解除待ちだったユーザーが実際にVCから退出したタイミングで呼ぶ。"""
    room = await asyncio.to_thread(store.get_room, guild.id, channel.id)
    if not room:
        return

    pending = room.get("pending_removal_user_ids") or []
    if str(user_id) not in pending:
        return

    now = _now_iso()
    async with get_room_lock(channel.id):
        room = await asyncio.to_thread(
            store.get_room, guild.id, channel.id
        )
        if not room:
            return

        await asyncio.to_thread(
            store.remove_pending_removal,
            guild.id,
            channel.id,
            user_id,
            now,
        )
        await apply_room_permissions(
            channel,
            guild,
            int(room["owner_id"]),
            room.get("invited_user_ids") or [],
            guild.me,
            reason="招待解除待ちユーザーの退出による権限確定",
        )


def build_invited_list_embed(
    room: Dict[str, Any], guild: discord.Guild, page: int, per_page: int
) -> Tuple[discord.Embed, int]:
    invited_ids = room.get("invited_user_ids") or []
    max_page = max(1, (len(invited_ids) - 1) // per_page + 1)
    page = max(0, min(page, max_page - 1))

    owner = guild.get_member(int(room["owner_id"]))
    owner_line = (
        f"・{owner.mention}" if owner else f"・{room['owner_id']}"
    )

    start = page * per_page
    page_ids = invited_ids[start : start + per_page]
    lines = []
    for uid in page_ids:
        member = guild.get_member(int(uid))
        lines.append(f"・{member.mention}" if member else f"・{uid}")

    embed = discord.Embed(
        title="招待済みユーザー確認",
        color=discord.Color.dark_purple(),
    )
    embed.add_field(name="👑 作成者", value=owner_line, inline=False)
    embed.add_field(
        name=f"👤 招待ユーザー（{len(invited_ids)}人）",
        value="\n".join(lines) if lines else "（いません）",
        inline=False,
    )
    if max_page > 1:
        embed.set_footer(text=f"{page + 1} / {max_page} ページ")

    return embed, max_page


# =============================
#    権限修復・カテゴリ復元
# =============================
async def repair_room_permissions(
    guild: discord.Guild, channel: discord.VoiceChannel
) -> List[str]:
    room = await asyncio.to_thread(store.get_room, guild.id, channel.id)
    if not room:
        return []

    return await apply_room_permissions(
        channel,
        guild,
        int(room["owner_id"]),
        room.get("invited_user_ids") or [],
        guild.me,
        reason="権限の自動修復",
    )


async def restore_category(
    guild: discord.Guild, channel: discord.VoiceChannel
) -> Optional[str]:
    """
    カテゴリ移動防止（8章）。本来のカテゴリへ戻す。
    戻り値: 何か処理をした場合の説明文字列（ログ用）。
    """
    room = await asyncio.to_thread(store.get_room, guild.id, channel.id)
    if not room:
        return None

    expected_category_id = int(room["destination_category_id"])
    if channel.category_id == expected_category_id:
        return None

    category = guild.get_channel(expected_category_id)
    now = _now_iso()

    if not isinstance(category, discord.CategoryChannel):
        if room.get("state") != "CATEGORY_MISSING":
            await asyncio.to_thread(
                store.set_state,
                guild.id,
                channel.id,
                "CATEGORY_MISSING",
                now,
            )
            # 該当メニューからの新規作成を停止（一覧表示用の目印）。
            # create_room自体もカテゴリ不在を検知して拒否するため、
            # このフラグは/list_vc_menusでの警告表示にのみ使う。
            await mark_menu_category_missing(
                guild.id, expected_category_id
            )
            await log_event(
                guild,
                "🚨 本来のカテゴリが見つかりません",
                "VCはそのままの位置に残し、新規作成メニューを停止します。"
                "管理者による設定修復をお待ちください。",
                チャンネル=channel.mention,
            )
        return "CATEGORY_MISSING"

    await channel.edit(
        category=category,
        reason="カテゴリ移動防止：本来のカテゴリへ復元",
    )
    await log_event(
        guild,
        "↩️ カテゴリ復元",
        "手動で移動されたVCを本来のカテゴリへ戻しました。",
        チャンネル=channel.mention,
        カテゴリ=category.name,
    )
    return "RESTORED"


# =============================
#    Bot起動時・定期整合性チェック
# =============================
async def reconcile_guild(bot: discord.Client, guild: discord.Guild) -> None:
    if not await asyncio.to_thread(is_rainbowl_guild, guild.id):
        return

    rooms = await asyncio.to_thread(store.list_active_rooms, guild.id)
    now_iso = _now_iso()

    for room in rooms:
        channel_id = int(room["channel_id"])
        owner_id = int(room["owner_id"])
        channel = guild.get_channel(channel_id)

        if channel is None:
            try:
                channel = await guild.fetch_channel(channel_id)
            except discord.NotFound:
                await asyncio.to_thread(
                    store.delete_room, guild.id, owner_id, channel_id
                )
                await log_event(
                    guild,
                    "🗑️ 外部削除の検知（Bot停止中）",
                    "Bot停止中にVCが削除されていたため、"
                    "アクティブルームDBから除去しました。",
                    チャンネルID=channel_id,
                )
                continue
            except discord.HTTPException:
                continue

        if not isinstance(channel, discord.VoiceChannel):
            continue

        await restore_category(guild, channel)
        await repair_room_permissions(guild, channel)

        human_count = human_member_count(channel)
        if human_count == 0:
            if not room.get("empty_since"):
                await asyncio.to_thread(
                    store.set_empty_since,
                    guild.id,
                    channel_id,
                    now_iso,
                    now_iso,
                )
            if room.get("state") == "ACTIVE":
                await asyncio.to_thread(
                    store.set_state,
                    guild.id,
                    channel_id,
                    "EMPTY_WAIT",
                    now_iso,
                )
        else:
            if room.get("empty_since"):
                await asyncio.to_thread(
                    store.set_empty_since,
                    guild.id,
                    channel_id,
                    None,
                    now_iso,
                )
            if room.get("state") == "EMPTY_WAIT":
                await asyncio.to_thread(
                    store.set_state,
                    guild.id,
                    channel_id,
                    "ACTIVE",
                    now_iso,
                )
