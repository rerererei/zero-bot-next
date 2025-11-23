import os
from dotenv import load_dotenv

load_dotenv()

# ===== Discord Bot =====
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# ===== RankCard (S3) =====
# S3 バケット名
RANKCARD_S3_BUCKET = os.getenv("RANKCARD_S3_BUCKET", "zero-bot")

# S3 のプレフィックス（フォルダ扱い）
# 例: "rankcard/" → S3上では "zero-bot/rankcard/xxx.png"
RANKCARD_S3_PREFIX = os.getenv("RANKCARD_S3_PREFIX", "rankcard/")
