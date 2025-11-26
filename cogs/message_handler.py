import discord
import datetime
import pytz
import os
import logging
from discord.ext import commands

from utils.channel_manager import ChannelManager
from config import debug_log
from data.guild_config_store import GuildConfigStore

# タイムゾーン設定
jst = pytz.timezone("Asia/Tokyo")

# 🔹 ギルド設定(DynamoDB) 用
config_store = GuildConfigStore()

# ログ保存ディレクトリとファイルパス
log_dir = "log"
os.makedirs(log_dir, exist_ok=True)
today_str = datetime.datetime.now(jst).strftime("%Y%m%d")
log_file_path = os.path.join(log_dir, f"message_handler_{today_str}.log")

# ログローテート処理（3日より古いログを削除）
for fname in os.listdir(log_dir):
    if fname.startswith("message_handler_") and fname.endswith(".log"):
        try:
            date_str = fname.replace("message_handler_", "").replace(".log", "")
            file_date = datetime.datetime.strptime(date_str, "%Y%m%d")
            if (datetime.datetime.now(jst) - file_date).days > 2:
                os.remove(os.path.join(log_dir, fname))
        except Exception:
            continue

# ログ設定（ファイル出力）
logger = logging.getLogger("message_handler")
logger.setLevel(logging.INFO)

# 重複防止
if not logger.handlers:
    file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
    formatter = logging.Formatter('[%(asctime)s] %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


class MessageHandlerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.channel_manager = ChannelManager(bot)

    def is_excluded(self, channel: discord.abc.GuildChannel) -> bool:
        """
        チャンネルが「除外カテゴリー」に属しているかどうかを、
        DynamoDB の guild_config から判定する。

        config.profile.excluded_category_ids = ["123", "456", ...]
        というイメージで保存しておく想定。
        """
        if channel is None or channel.category is None:
            return False

        guild = channel.guild
        if guild is None:
            return False

        cfg = config_store.get_config(guild.id) or {}
        profile_cfg = cfg.get("profile") or {}

        raw_ids = profile_cfg.get("excluded_category_ids") or []

        # 文字列／数値どちらでも扱えるように int 化してセットに
        excluded_ids = set()
        for x in raw_ids:
            try:
                excluded_ids.add(int(x))
            except (TypeError, ValueError):
                continue

        return channel.category.id in excluded_ids

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """ボイスチャンネルのテキストチャットのメッセージのみ転記"""
        now = datetime.datetime.now(jst).strftime("%Y-%m-%d %H:%M:%S")

        logger.info(f"[MESSAGE][{message.channel.name}][{message.author.display_name}] {message.content}")
        image_urls = [attachment.url for attachment in message.attachments]
        if image_urls:
            logger.info(f"[IMAGE][{message.channel.name}][{message.author.display_name}] {image_urls[0]}")

        if message.author.bot:
            return

        guild = message.guild
        if guild is None:
            debug_log("ギルド情報が取得できないため無視")
            return

        if not isinstance(message.channel, discord.VoiceChannel):
            debug_log(f"{message.channel.name} はボイスチャンネルではないため無視")
            return

        # 🔹 除外カテゴリー判定を DB ベースに変更
        if self.is_excluded(message.channel):
            cat_id = message.channel.category.id if message.channel.category else "N/A"
            debug_log(f"[SKIP] `{message.channel.name}` は除外カテゴリー (`{cat_id}`) に属するため無視")
            return

        # 転記先テキストチャンネルの取得 or 作成
        target_channel = await self.channel_manager.get_or_create_text_channel(guild, message.channel)
        debug_log(f"転記先チャンネル: {target_channel.name} ({target_channel.id})")

        message_time_jst = message.created_at.replace(tzinfo=pytz.utc).astimezone(jst).strftime("%Y/%m/%d %H:%M:%S")

        embed = discord.Embed(
            description=message.content,
            color=0x82cded,
        )
        embed.set_author(
            name=f"{message.author.display_name}   {message_time_jst}",
            icon_url=message.author.display_avatar.url
        )

        if image_urls:
            embed.set_image(url=image_urls[0])

        await target_channel.send(embed=embed)
        debug_log(f"メッセージを転記完了: {message.content}")

        # 2枚目以降の画像
        for img_url in image_urls[1:]:
            image_embed = discord.Embed(
                color=0x82cded,
            )
            image_embed.set_author(
                name=f"{message.author.display_name}   {message_time_jst}",
                icon_url=message.author.display_avatar.url
            )
            image_embed.set_image(url=img_url)

            await target_channel.send(embed=image_embed)
            debug_log(f"追加の画像を転記: {img_url}")

        await self.bot.process_commands(message)


async def setup(bot):
    await bot.add_cog(MessageHandlerCog(bot))
