import os
from dotenv import load_dotenv
import discord

load_dotenv()

# ───────────────
#  Discord Bot 基本
# ───────────────
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# ───────────────
#  RankCard（S3）
# ───────────────
RANKCARD_S3_BUCKET = os.getenv("RANKCARD_S3_BUCKET", "zero-bot")
RANKCARD_S3_PREFIX = os.getenv("RANKCARD_S3_PREFIX", "rankcard/")

# ───────────────
#  Discord Intents
# ───────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True
intents.guilds = True

# ───────────────
#  Debug Mode
# ───────────────
DEBUG_MODE = os.getenv("DEBUG_MODE", "False").lower() == "true"

def debug_log(message: str):
    if DEBUG_MODE:
        print(f"[DEBUG] {message}")
