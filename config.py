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

# ───────────────
#  BDSM Check
# ───────────────
# BDSM診断結果URL投稿チャンネル
BDSM_MALE_URL_CHANNEL_ID = 1527693775172931604
BDSM_FEMALE_URL_CHANNEL_ID = 1527693836422348961

# /bdsm_check 実行ログ投稿先
BDSM_COMMAND_LOG_CHANNEL_ID = 1527958108519927948

# プロフィール投稿チャンネル
# 実際のチャンネルIDに置き換える
BDSM_MALE_PROFILE_CHANNEL_ID = 0
BDSM_FEMALE_PROFILE_CHANNEL_ID = 0