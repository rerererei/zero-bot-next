import discord
from discord.ext import commands
from config import DISCORD_BOT_TOKEN


class ZeroBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.voice_states = True
        intents.message_content = True  # テキストXP用
        intents.members = True          # ⭐ 追加：VCメンバー取得用

        super().__init__(
            command_prefix="ZB",
            intents=intents,
        )

    async def setup_hook(self):
        # VCレベリング
        await self.load_extension("cogs.voice_leveling")
        # テキストレベリング
        await self.load_extension("cogs.text_leveling")
        # zbコマンド（Cog側でコマンド登録）
        await self.load_extension("cogs.zb_commands")
        await self.load_extension("cogs.zbadmin_commands")
        # スラッシュコマンド同期
        await self.tree.sync()
        print("✅ スラッシュコマンド同期完了")

    async def on_ready(self):
        print(f"✅ ログインしました: {self.user} ({self.user.id})")

def main():
    if not DISCORD_BOT_TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN が設定されていないよ！")

    bot = ZeroBot()
    bot.run(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()
