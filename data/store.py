# data/store.py
from typing import Dict, Optional

# from data.backends.json_store import JsonStore
# from data.backends.memory_store import MemoryStore
from data.backends.dynamo_store import DynamoStore
from data.guild_config_store import GuildConfigStore

# =========================
#  永続化バックエンド選択
# =========================
store = DynamoStore(table_name="zero_bot_xp")
guild_config_store = GuildConfigStore()


# =========================
#  XP 読み書き用ラッパ関数
# =========================
def add_voice_xp(gid: int, uid: int, xp: float) -> None:
    store.add_voice_xp(gid, uid, xp)

def get_voice_xp(gid: int, uid: int) -> float:
    return store.get_voice_xp(gid, uid)

def add_text_xp(gid: int, uid: int, xp: float) -> None:
    store.add_text_xp(gid, uid, xp)

def get_text_xp(gid: int, uid: int) -> float:
    return store.get_text_xp(gid, uid)

def get_guild_user_stats(gid: int) -> Dict[int, Dict[str, float]]:
    return store.get_guild_user_stats(gid)

# ★ 統計情報向けラッパー追記
def get_voice_meta(gid: int, uid: int) -> Dict[str, float]:
    return store.get_voice_meta(gid, uid)

def update_voice_meta(gid: int, uid: int, meta: Dict[str, float]) -> None:
    store.update_voice_meta(gid, uid, meta)

# =========================
#  レベル計算ロジック
# =========================
LEVEL_BASE_XP = 20.0  # XP_to_next(L) = LEVEL_BASE_XP * L


def calc_level_from_xp(xp: float) -> tuple[int, float, float]:
    """
    XP からレベルを計算する。

    戻り値:
        level: 現在のレベル
        current_xp_in_level: 今のレベルの中でたまっているXP
        next_level_requirement: このレベルから次のレベルに必要なXP
    """
    if xp <= 0:
        return 1, 0.0, LEVEL_BASE_XP  # Lv1, 0/20XP

    level = 1
    remaining = xp
    need = LEVEL_BASE_XP * level  # このレベルから次へ必要なXP

    # XP_to_next(L) = 20 * L で、足りる限りレベルを上げていく
    while remaining >= need:
        remaining -= need
        level += 1
        need = LEVEL_BASE_XP * level

    # remaining がそのレベルの中で貯まっているXP
    return level, remaining, need

def get_rank_bg_key(gid: int, uid: int) -> str:
    # 2) DynamoDB のユーザー個別設定
    user_bg_key = store.get_rank_bg_key(gid, uid)
    if user_bg_key:
        return user_bg_key

    # 1) ギルド設定（zero_bot_guild_config）
    guild_cfg = guild_config_store.get_config(gid) or {}
    rankcard_cfg = guild_cfg.get("rankcard") or {}

    guild_bg_key = rankcard_cfg.get("rank_bg_key")
    if guild_bg_key:
        return guild_bg_key

    # 3) fallback
    return "Default"