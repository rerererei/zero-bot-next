import time
import discord
from discord.ext import commands

from data.store import add_text_xp


COOLDOWN_SECONDS = 10  # 1ユーザーあたり10秒クールダウン


def calc_text_xp(message: discord.Message) -> int:
    """
    テキストXP計算ロジック
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


class TextLeveling(commands.Cog):
    """テキストXP付与を担当するCog"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # {(guild_id, user_id): last_timestamp}
        self._last_given_ts: dict[tuple[int, int], float] = {}

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # DM/自分/他botは無視
        if message.author.bot:
            return
        if message.guild is None:
            return

        guild_id = message.guild.id
        user_id = message.author.id

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
        self._last_given_ts[key] = now

        # デバッグ用
        # print(f"[TextLeveling] {guild_id=}, {user_id=} に {xp} XP 付与")


async def setup(bot: commands.Bot):
    await bot.add_cog(TextLeveling(bot))
