import discord
from discord import app_commands
import re
import json
import os
import datetime

from config import debug_log
from data.guild_config_store import GuildConfigStore
from data.store import calc_level_from_xp

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


async def voice_users_autocomplete(
    interaction: discord.Interaction, current: str
):
    """
    ボイスチャンネルのユーザーをオートコンプリート（DB対応版）

    - ギルドごとの設定は DynamoDB (guild_config_store) から取得
    - profile セクション内:
        {
          "profile": {
            "excluded_voice_channel_ids": ["123456789012345678", ...]
          }
        }
      のような形を想定
    """

    guild = interaction.guild
    if guild is None:
        debug_log("[AUTO] サーバー情報なし")
        return []

    guild_id = guild.id

    # 🔹 DB からギルド設定を取得
    cfg = config_store.get_config(guild_id) or {}
    profile_cfg = cfg.get("profile") or {}

    # DB に未設定なら空扱い
    raw_excluded = profile_cfg.get("excluded_voice_channel_ids", [])
    try:
        excluded_voice_channels = [int(c) for c in raw_excluded]
    except (TypeError, ValueError):
        excluded_voice_channels = []

    current_lower = (current or "").lower()
    voice_members: list[str] = []

    debug_log(f"[AUTO] 除外VC = {excluded_voice_channels}")

    for vc in guild.voice_channels:
        # 🔹 DB で除外指定された VC をスキップ
        if vc.id in excluded_voice_channels:
            debug_log(f"[AUTO] 除外VCスキップ: {vc.name} ({vc.id})")
            continue

        for member in vc.members:
            if current_lower in member.display_name.lower():
                voice_members.append(member.display_name)

    debug_log(f"[AUTO] 候補 = {voice_members[:25]}")

    return [
        app_commands.Choice(name=name, value=name)
        for name in voice_members[:25]
    ]

# ============================================
# XP / レベル関連ヘルパ
# ============================================

def _xp_for_level(target_level: int) -> float:
    """
    指定レベルになるために必要な『通算XP』を逆算する。

    calc_level_from_xp を使って二分探索で求めるので、
    XPカーブの実装に依存しない。
    """
    if target_level <= 1:
        return 0.0

    # ざっくり上限を探す（指数的に増やしていく）
    lo = 0.0
    hi = 100.0

    while True:
        lv, _, _ = calc_level_from_xp(hi)
        if lv >= target_level:
            break
        hi *= 2
        if hi > 10_000_000:  # 上限保険
            break

    # lo..hi の範囲で「そのレベルになる最小XP」を二分探索
    for _ in range(40):  # 精度十分
        mid = (lo + hi) / 2
        lv, _, _ = calc_level_from_xp(mid)
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
