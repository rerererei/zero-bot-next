import discord
import io
import re
import json
import os
import datetime
from typing import Optional

from config import debug_log
from data.guild_config_store import GuildConfigStore
from data.xp_router import calc_level_from_xp

# ============================================
# プロフィールメッセージ（従来の JSON 保存版）
# ============================================

PROFILE_MESSAGE_PATH = "profile_messages.json"


def load_profile_messages():
    """
    旧仕様互換：
    profile_messages.json からプロフィールメッセージ情報を読み込む。

    戻り値イメージ:
        {
            "123456789012345678": "https://discord.com/channels/....",
            "987654321098765432": "https://discord.com/channels/....",
            ...
        }
    """
    if os.path.exists(PROFILE_MESSAGE_PATH):
        try:
            with open(PROFILE_MESSAGE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            debug_log(f"[PROFILE] load_profile_messages 失敗: {e}")
            return {}
    return {}

def save_profile_messages(data: dict):
    """
    旧仕様互換：
    profile_messages.json にプロフィールメッセージ情報を書き出す。
    """
    try:
        with open(PROFILE_MESSAGE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        debug_log(f"[PROFILE] save_profile_messages 失敗: {e}")


# ============================================
# DynamoDB ギルド設定
# ============================================

config_store = GuildConfigStore()


def normalize_voice_channel_name(name: str) -> str:
    """ボイスチャンネル名を比較用に正規化"""
    name = re.sub(r"\s+", " ", name).strip()
    return name


def normalize_text_channel_name(name: str) -> str:
    """テキストチャンネル名を比較用に正規化"""
    name = re.sub(r"\s+", "-", name.strip())
    name = re.sub(r"-+", "-", name)
    return name.strip("-")


AVATAR_ATTACHMENT_FILENAME = "avatar.png"


async def fetch_avatar_png(user: discord.abc.User) -> Optional[bytes]:
    """アイコン画像（64px・PNG）をダウンロードする。失敗したら None"""
    try:
        return await user.display_avatar.with_format("png").with_size(64).read()
    except discord.HTTPException as e:
        debug_log(f"[AVATAR] アイコン取得失敗のためURLを使います: {user} ({e})")
        return None


def set_author_with_uploaded_avatar(
    embed: discord.Embed, name: str, user: discord.abc.User, avatar_png: Optional[bytes], files: list
) -> None:
    """
    Embed の author アイコンを、アイコン画像をアップロードして設定する（別サーバーのアーカイブ用）。
    アイコンを変えたり退会したりしても、アーカイブ側で表示されなくならないようにするため。
    画像は files に追加するので、send(files=files) で一緒に送ること。
    avatar_png が None（取得失敗）の場合は、これまで通りアイコンのURLを使う。
    """
    if avatar_png is None:
        embed.set_author(name=name, icon_url=user.display_avatar.url)
        return

    files.append(discord.File(io.BytesIO(avatar_png), filename=AVATAR_ATTACHMENT_FILENAME))
    embed.set_author(name=name, icon_url=f"attachment://{AVATAR_ATTACHMENT_FILENAME}")


def get_voice_connected_members(guild: discord.Guild) -> list[discord.Member]:
    """
    サーバー内で現在いずれかのVCに接続しているメンバー一覧を返す（DB対応版）。

    - ギルドごとの設定は DynamoDB (guild_config_store) から取得
    - profile セクション内:
        {
          "profile": {
            "excluded_voice_channel_ids": ["123456789012345678", ...],
            "excluded_voice_category_ids": ["123456789012345678", ...]
          }
        }
      で指定されたVC・カテゴリー配下のVCは除外する
      （excluded_category_ids はVC入退室ログ等の別機能用キーのため使わない）
    """

    cfg = config_store.get_config(guild.id) or {}
    profile_cfg = cfg.get("profile") or {}

    raw_excluded_channels = profile_cfg.get("excluded_voice_channel_ids", [])
    try:
        excluded_voice_channels = {int(c) for c in raw_excluded_channels}
    except (TypeError, ValueError):
        excluded_voice_channels = set()

    raw_excluded_categories = profile_cfg.get("excluded_voice_category_ids", [])
    try:
        excluded_voice_categories = {int(c) for c in raw_excluded_categories}
    except (TypeError, ValueError):
        excluded_voice_categories = set()

    members: list[discord.Member] = []

    for vc in guild.voice_channels:
        if vc.id in excluded_voice_channels:
            continue
        if vc.category_id in excluded_voice_categories:
            continue

        members.extend(m for m in vc.members if not m.bot)

    return members

# ============================================
# XP / レベル関連ヘルパ
# ============================================

def _xp_for_level(target_level: int, guild_id: int) -> float:
    """
    指定レベルになるために必要な『通算XP』を逆算する。

    calc_level_from_xp を使って二分探索で求めるので、
    XPカーブの実装に依存しない。guild_idに応じたカーブ
    （generic/rainbowl）を使う。
    """
    if target_level <= 1:
        return 0.0

    # ざっくり上限を探す（指数的に増やしていく）
    lo = 0.0
    hi = 100.0

    while True:
        lv, _, _ = calc_level_from_xp(guild_id, hi)
        if lv >= target_level:
            break
        hi *= 2
        if hi > 10_000_000:  # 上限保険
            break

    # lo..hi の範囲で「そのレベルになる最小XP」を二分探索
    for _ in range(40):  # 精度十分
        mid = (lo + hi) / 2
        lv, _, _ = calc_level_from_xp(guild_id, mid)
        if lv >= target_level:
            hi = mid
        else:
            lo = mid

    return hi

# ===== JST関連ユーティリティ =====
JST = datetime.timezone(datetime.timedelta(hours=9))

def jst_now() -> datetime.datetime:
    """
    JST の timezone-aware な現在時刻を返す。
    """
    return datetime.datetime.now(JST)


def format_account_age(created_at: datetime.datetime) -> str:
    """アカウント作成日から「n年nヶ月」形式の経過期間を作る。"""
    now = datetime.datetime.now(datetime.timezone.utc)
    delta_days = (now - created_at).days

    years = delta_days // 365
    months = (delta_days % 365) // 30

    if years and months:
        return f"{years}年{months}ヶ月"

    if years:
        return f"{years}年"

    if months:
        return f"{months}ヶ月"

    return f"{delta_days}日"
