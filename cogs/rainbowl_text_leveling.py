# cogs/rainbowl_text_leveling.py
#
# rainbowl専用のテキストXP付与。cogs/text_leveling.pyのフォーク。
# guild_configに"rainbowl"名前空間を持つギルドだけを処理し、
# XPは zero_bot_rainbowl_xp（data/rainbowl/xp_store.py）へ書き込む。
# 汎用のtext_leveling.py側は、rainbowl設定を持つギルドを処理対象から
# 除外しているため、二重付与にはならない。

import time
import discord
from discord.ext import commands

from data.rainbowl.xp_store import add_text_xp
from data.text_daily_store import add_daily_text_activity
from data.guild_config_store import GuildConfigStore


COOLDOWN_SECONDS = 10  # 1ユーザーあたり10秒クールダウン


def calc_text_xp(message: discord.Message) -> int:
    """
    テキストXP計算ロジック（rainbowl専用。汎用側とは独立に調整する）
    - 1〜79文字   → 1XP
    - 80〜159文字 → 2XP
    - 160〜200文字→ 3XP（それ以上書いても3）
    """
    content = message.content or ""
    length = len(content)

    if length == 0:
        return 0

    effective_len = min(length, 200)
    step = effective_len // 80  # 0,1,2…
    xp = 1 + step
    if xp > 3:
        xp = 3
    return xp


class RainbowlTextLeveling(commands.Cog):
    """rainbowl専用 テキストXP付与を担当するCog"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.guild_config_store = GuildConfigStore()
        # {(guild_id, user_id): last_timestamp}
        self._last_given_ts: dict[tuple[int, int], float] = {}

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if message.guild is None:
            return

        guild_id = message.guild.id
        user_id = message.author.id

        cfg = self.guild_config_store.get_config(guild_id) or {}
        if not cfg.get("rainbowl"):
            return

        # クールダウンチェック
        now = time.time()
        key = (guild_id, user_id)
        last_ts = self._last_given_ts.get(key)
        if last_ts is not None and (now - last_ts) < COOLDOWN_SECONDS:
            return  # クールダウン中

        xp = calc_text_xp(message)
        if xp <= 0:
            return

        add_text_xp(guild_id, user_id, xp)
        add_daily_text_activity(guild_id, user_id, xp=xp, message_count=1)
        self._last_given_ts[key] = now


async def setup(bot: commands.Bot):
    await bot.add_cog(RainbowlTextLeveling(bot))
