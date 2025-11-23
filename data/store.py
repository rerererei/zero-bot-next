# data/store.py
from typing import Dict

from data.backends.json_store import JsonStore
# from data.backends.memory_store import MemoryStore
# from data.backends.dynamo_store import DynamoStore


# =========================
#  永続化バックエンド選択
# =========================
# 今は JSON 永続化を使う
store = JsonStore("data/zero_bot_xp.json")
# 将来 Dynamo にするときはここだけ変えるイメージ
# store = DynamoStore(table_name="zero_bot_xp")


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
