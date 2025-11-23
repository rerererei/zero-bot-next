# data/store_base.py
from typing import Dict

class BaseStore:
    def add_voice_xp(self, guild_id: int, user_id: int, xp: float):
        raise NotImplementedError

    def get_voice_xp(self, guild_id: int, user_id: int) -> float:
        raise NotImplementedError

    def add_text_xp(self, guild_id: int, user_id: int, xp: float):
        raise NotImplementedError

    def get_text_xp(self, guild_id: int, user_id: int) -> float:
        raise NotImplementedError

    def get_guild_user_stats(self, guild_id: int) -> Dict[int, Dict[str, float]]:
        raise NotImplementedError
