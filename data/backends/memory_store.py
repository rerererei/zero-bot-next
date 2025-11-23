# data/backends/memory_store.py

from typing import Dict
from data.store_base import BaseStore

class MemoryStore(BaseStore):
    def __init__(self):
        # {guild_id:{user_id:{voice_xp:..., text_xp:...}}}
        self.data: Dict[int, Dict[int, Dict[str, float]]] = {}

    def _ensure_user(self, guild_id: int, user_id: int):
        g = self.data.setdefault(guild_id, {})
        u = g.setdefault(user_id, {"voice_xp": 0.0, "text_xp": 0.0})
        return u

    def add_voice_xp(self, guild_id, user_id, xp):
        u = self._ensure_user(guild_id, user_id)
        u["voice_xp"] += xp

    def get_voice_xp(self, guild_id, user_id):
        return self.data.get(guild_id, {}).get(user_id, {}).get("voice_xp", 0.0)

    def add_text_xp(self, guild_id, user_id, xp):
        u = self._ensure_user(guild_id, user_id)
        u["text_xp"] += xp

    def get_text_xp(self, guild_id, user_id):
        return self.data.get(guild_id, {}).get(user_id, {}).get("text_xp", 0.0)

    def get_guild_user_stats(self, guild_id):
        return self.data.get(guild_id, {})

