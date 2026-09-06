# data/xp_router.py
"""
XPの読み書きを、ギルドごとに適切なバックエンドへ振り分けるルーター。

guild_configに"rainbowl"名前空間を持つギルドは data.rainbowl.xp_store
（zero_bot_rainbowl_xp）、それ以外は data.store（zero_bot_xp）を使う。

RANK CARD描画・/zbadmin系コマンドなど、複数ギルドを横断して扱う
汎用UI側から使うためのモジュール。rainbowl専用Cog（cogs/rainbowl_voice_leveling.py
等）は振り分け不要なので data.rainbowl.xp_store を直接使う。
"""

from typing import Dict, Tuple

from data import store as _generic
from data.rainbowl import xp_store as _rainbowl
from data.guild_config_store import GuildConfigStore

_guild_config_store = GuildConfigStore()


def _backend_for(guild_id: int):
    cfg = _guild_config_store.get_config(guild_id) or {}
    return _rainbowl if cfg.get("rainbowl") else _generic


def get_voice_xp(guild_id: int, user_id: int) -> float:
    return _backend_for(guild_id).get_voice_xp(guild_id, user_id)


def add_voice_xp(guild_id: int, user_id: int, xp: float) -> None:
    _backend_for(guild_id).add_voice_xp(guild_id, user_id, xp)


def get_text_xp(guild_id: int, user_id: int) -> float:
    return _backend_for(guild_id).get_text_xp(guild_id, user_id)


def add_text_xp(guild_id: int, user_id: int, xp: float) -> None:
    _backend_for(guild_id).add_text_xp(guild_id, user_id, xp)


def get_guild_user_stats(guild_id: int) -> Dict[int, Dict[str, float]]:
    return _backend_for(guild_id).get_guild_user_stats(guild_id)


def get_voice_meta(guild_id: int, user_id: int) -> Dict[str, float]:
    return _backend_for(guild_id).get_voice_meta(guild_id, user_id)


def get_rank_bg_key(guild_id: int, user_id: int) -> str:
    return _backend_for(guild_id).get_rank_bg_key(guild_id, user_id)


def calc_level_from_xp(guild_id: int, xp: float) -> Tuple[int, float, float]:
    """
    ギルドごとのレベルカーブでXPからレベルを計算する。
    generic/rainbowlは開始時点では同じ式だが、以後は個別に調整され得るため
    guild_idを受け取って対応するカーブを使う。
    """
    return _backend_for(guild_id).calc_level_from_xp(xp)
