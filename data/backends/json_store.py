# data/backends/json_store.py

import json
import os
from typing import Dict
from data.store_base import BaseStore


class JsonStore(BaseStore):

    def __init__(self, path: str):
        self.path = path

        # XPデータ
        self.data: Dict[int, Dict[int, Dict[str, float]]] = {}

        # VoiceMeta（統計データ）
        self.meta: Dict[int, Dict[int, Dict[str, float]]] = {}

        self._load()

    # -----------------------------
    # JSON 読み込み / 保存
    # -----------------------------
    def _load(self):
        if not os.path.exists(self.path):
            self.data = {}
            self.meta = {}
            return

        with open(self.path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        # XP
        raw_data = raw.get("data", {})
        self.data = {
            int(gid): {
                int(uid): {
                    "voice_xp": float(stats.get("voice_xp", 0.0)),
                    "text_xp": float(stats.get("text_xp", 0.0)),
                }
                for uid, stats in users.items()
            }
            for gid, users in raw_data.items()
        }

        # Meta（統計）
    def _load(self):
        if not os.path.exists(self.path):
            self.data = {}
            self.meta = {}
            return

        with open(self.path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        # XP
        raw_data = raw.get("data", {})
        self.data = {
            int(gid): {
                int(uid): {
                    "voice_xp": float(stats.get("voice_xp", 0.0)),
                    "text_xp": float(stats.get("text_xp", 0.0)),
                }
                for uid, stats in users.items()
            }
            for gid, users in raw_data.items()
        }

        # ★ Meta（統計）ここを更新
        raw_meta = raw.get("meta", {})
        self.meta = {}

        for gid, users in raw_meta.items():
            gid_int = int(gid)
            self.meta[gid_int] = {}

            for uid, m in users.items():
                uid_int = int(uid)

                total_time = float(m.get("total_time", 0.0))
                solo_time = float(m.get("solo_time", 0.0))
                small_group_time = float(m.get("small_group_time", 0.0))
                mid_group_time = float(m.get("mid_group_time", 0.0))
                big_group_time = float(m.get("big_group_time", 0.0))
                muted_time = float(m.get("muted_time", 0.0))
                max_member_count = int(m.get("max_member_count", 0))

                # hour_buckets（既に入れてると思うけど一応）
                hb = m.get("hour_buckets")
                if isinstance(hb, list) and len(hb) == 24:
                    hour_buckets = [float(x) for x in hb]
                else:
                    hour_buckets = [0.0] * 24

                # ★ ここが新規：pair_time を読む
                raw_pair = m.get("pair_time", {})
                pair_time = {}
                if isinstance(raw_pair, dict):
                    for k, v in raw_pair.items():
                        # key は文字列IDにそろえておく
                        pair_time[str(k)] = float(v)

                self.meta[gid_int][uid_int] = {
                    "total_time": total_time,
                    "solo_time": solo_time,
                    "small_group_time": small_group_time,
                    "mid_group_time": mid_group_time,
                    "big_group_time": big_group_time,
                    "muted_time": muted_time,
                    "max_member_count": max_member_count,
                    "hour_buckets": hour_buckets,
                    "pair_time": pair_time,  # ★ ここ
                }

    def _save(self):
        out_data = {}
        for gid, users in self.data.items():
            out_data[str(gid)] = {}
            for uid, stats in users.items():
                out_data[str(gid)][str(uid)] = {
                    "voice_xp": stats.get("voice_xp", 0.0),
                    "text_xp": stats.get("text_xp", 0.0),
                }

        out_meta = {}
        for gid, users in self.meta.items():
            out_meta[str(gid)] = {}
            for uid, m in users.items():
                out_meta[str(gid)][str(uid)] = {
                    "total_time": m.get("total_time", 0.0),
                    "solo_time": m.get("solo_time", 0.0),
                    "small_group_time": m.get("small_group_time", 0.0),
                    "mid_group_time": m.get("mid_group_time", 0.0),
                    "big_group_time": m.get("big_group_time", 0.0),
                    "muted_time": m.get("muted_time", 0.0),
                    "max_member_count": m.get("max_member_count", 0),
                    "hour_buckets": m.get("hour_buckets", [0.0] * 24),
                    "pair_time": m.get("pair_time", {}),  # ★ ここ
                }

        obj = {"data": out_data, "meta": out_meta}

        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)

    # -----------------------------
    # XP 操作
    # -----------------------------
    def _ensure_user(self, guild_id: int, user_id: int):
        g = self.data.setdefault(guild_id, {})
        u = g.setdefault(user_id, {"voice_xp": 0.0, "text_xp": 0.0})

        mg = self.meta.setdefault(guild_id, {})
        mg.setdefault(
            user_id,
            {
                "total_time": 0.0,
                "solo_time": 0.0,
                "small_group_time": 0.0,
                "mid_group_time": 0.0,
                "big_group_time": 0.0,
                "muted_time": 0.0,
                "max_member_count": 0,
                "hour_buckets": [0.0] * 24,
                "pair_time": {},          # ★ ここ追加
            },
        )
        return u

    def add_voice_xp(self, guild_id, user_id, xp):
        u = self._ensure_user(guild_id, user_id)
        u["voice_xp"] += xp
        self._save()

    def get_voice_xp(self, guild_id, user_id):
        return self.data.get(guild_id, {}).get(user_id, {}).get("voice_xp", 0.0)

    def add_text_xp(self, guild_id, user_id, xp):
        u = self._ensure_user(guild_id, user_id)
        u["text_xp"] += xp
        self._save()

    def get_text_xp(self, guild_id, user_id):
        return self.data.get(guild_id, {}).get(user_id, {}).get("text_xp", 0.0)

    def get_guild_user_stats(self, guild_id):
        return self.data.get(guild_id, {})

    # -----------------------------
    # Voice Meta 操作
    # -----------------------------
    def get_voice_meta(self, guild_id: int, user_id: int) -> Dict[str, float]:
        self._ensure_user(guild_id, user_id)
        return self.meta[guild_id][user_id]

    def update_voice_meta(self, guild_id: int, user_id: int, new_data: Dict[str, float]):
        self._ensure_user(guild_id, user_id)

        for k, v in new_data.items():
            self.meta[guild_id][user_id][k] = v

        self._save()
