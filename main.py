import discord
from discord.ext import commands
from config import DISCORD_BOT_TOKEN


class ZeroBot(commands.Bot):
    def __init__(self):
        # ===== Intents 設定 =====
        intents = discord.Intents.default()
        intents.message_content = True      # テキストレベリング／ログ用
        intents.members = True             # メンバー情報（ロール判定・プロフィール等）
        intents.voice_states = True        # ボイス入退室検知
        intents.guilds = True              # ギルド情報（カテゴリ・チャンネル）

        super().__init__(
            command_prefix="ZB",  # prefixコマンドはほぼ使ってないけど一応
            intents=intents,
        )

    async def setup_hook(self):
        """Bot 起動時の初期化 & Cog ロード"""
        extensions = [
            # レベリング系
            "cogs.voice_leveling",
            "cogs.text_leveling",

            # ZB コマンド系
            "cogs.zb_commands",
            "cogs.zbadmin_commands",

            # おやんも系
            "cogs.oyanmo",

            # ログ・アーカイブ・イベント関連
            "cogs.voice_events",
            "cogs.message_handler",
            "cogs.archive_manager",
        ]

        for ext in extensions:
            try:
                await self.load_extension(ext)
                print(f"✅ Cog loaded: {ext}")
            except Exception as e:
                print(f"❌ Failed to load {ext}: {e}")

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
