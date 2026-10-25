import os

import discord
import datetime
import pytz
import logging
from discord.ext import commands

from utils.channel_manager import ChannelManager
from utils.helpers import fetch_avatar_png, set_author_with_uploaded_avatar
from config import debug_log
from data.guild_config_store import GuildConfigStore

# タイムゾーン設定
jst = pytz.timezone("Asia/Tokyo")

# 🔹 ギルド設定(DynamoDB) 用
config_store = GuildConfigStore()

# ロガー設定（標準出力のみ）
logger = logging.getLogger("message_handler")
logger.setLevel(logging.INFO)

# 重複防止
if not logger.handlers:
    stream_handler = logging.StreamHandler()  # ファイルではなく標準出力に出す
    formatter = logging.Formatter('[%(asctime)s] %(levelname)s - %(message)s')
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)


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

    async def _download_attachments(self, attachments, filesize_limit: int) -> list:
        """
        添付ファイルをダウンロードして discord.File にする。
        戻り値の各要素:
          file: discord.File（サイズ超過・取得失敗時は None）
          embed_image: Embed に画像として埋め込むか
          filename / url: 転記できなかったとき用
        """
        results = []
        for i, attachment in enumerate(attachments):
            is_image = (attachment.content_type or "").startswith("image/")
            result = {
                "file": None,
                "embed_image": False,
                "filename": attachment.filename,
                "url": attachment.url,
            }

            if attachment.size > filesize_limit:
                debug_log(f"[ATTACHMENT] サイズ超過のため転記しません: {attachment.filename} ({attachment.size} bytes)")
                results.append(result)
                continue

            try:
                if is_image and not attachment.is_spoiler():
                    # attachment:// で参照するため、ASCIIだけのファイル名にする
                    ext = os.path.splitext(attachment.filename)[1].lower()
                    result["file"] = await attachment.to_file(filename=f"{i}_{attachment.id}{ext}", use_cached=True)
                    result["embed_image"] = True
                else:
                    result["file"] = await attachment.to_file(use_cached=True)
            except discord.HTTPException as e:
                debug_log(f"[ATTACHMENT] ダウンロード失敗: {attachment.filename} ({e})")

            results.append(result)
        return results

    async def _send_with_urls(self, message: discord.Message, target_channel: discord.TextChannel, author_name: str):
        """元の添付URLを Embed に埋め込んで転記する（同じサーバー内のアーカイブ用）"""
        image_urls = [attachment.url for attachment in message.attachments]

        # 1通目（本文＋1枚目の画像）
        embed = discord.Embed(
            description=message.content,
            color=0x82cded,
        )
        embed.set_author(name=author_name, icon_url=message.author.display_avatar.url)

        if image_urls:
            embed.set_image(url=image_urls[0])

        await target_channel.send(embed=embed)
        debug_log(f"メッセージを転記完了: {message.content}")

        # 2枚目以降の画像は別Embedで送信
        for img_url in image_urls[1:]:
            image_embed = discord.Embed(
                color=0x82cded,
            )
            image_embed.set_author(name=author_name, icon_url=message.author.display_avatar.url)
            image_embed.set_image(url=img_url)

            await target_channel.send(embed=image_embed)
            debug_log(f"追加の画像を転記: {img_url}")

    async def _send_with_reupload(self, message: discord.Message, target_channel: discord.TextChannel, author_name: str):
        """添付をアップロードし直して転記する（別サーバーのアーカイブ用）"""
        files = await self._download_attachments(message.attachments, target_channel.guild.filesize_limit)
        # アイコンも添付で送る（Embed ごとに同じ画像を付け直す）
        avatar_png = await fetch_avatar_png(message.author)

        # 1通目（本文＋1枚目の画像）
        first_image_index = next((i for i, f in enumerate(files) if f["embed_image"]), None)
        embed = discord.Embed(
            description=message.content,
            color=0x82cded,
        )
        send_files = []
        set_author_with_uploaded_avatar(embed, author_name, message.author, avatar_png, send_files)

        if first_image_index is not None:
            first_file = files.pop(first_image_index)
            embed.set_image(url=f"attachment://{first_file['file'].filename}")
            send_files.append(first_file["file"])
        await target_channel.send(embed=embed, files=send_files)
        debug_log(f"メッセージを転記完了: {message.content}")

        # 2つ目以降の添付は1つずつ送信
        for f in files:
            if f["embed_image"]:
                image_embed = discord.Embed(color=0x82cded)
                send_files = []
                set_author_with_uploaded_avatar(image_embed, author_name, message.author, avatar_png, send_files)
                image_embed.set_image(url=f"attachment://{f['file'].filename}")
                send_files.append(f["file"])
                await target_channel.send(embed=image_embed, files=send_files)
            elif f["file"] is not None:
                # 画像以外・スポイラー画像はファイルとしてそのまま送る
                await target_channel.send(content=author_name, file=f["file"])
            else:
                # サイズ超過・取得失敗は元URLを残す（元メッセージ削除後は開けなくなる）
                await target_channel.send(
                    content=f"{author_name}\n📎 {f['filename']}（転記できませんでした）\n{f['url']}"
                )
            debug_log(f"添付を転記: {f['filename']}")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """ボイスチャンネルのテキストチャットのメッセージのみ転記"""

        # ログ出力（標準出力に流れる）
        # logger.info(f"[MESSAGE][{message.channel.name}][{message.author.display_name}] {message.content}")

        # Bot 自身のメッセージは無視
        if message.author.bot:
            return

        guild = message.guild
        if guild is None:
            debug_log("ギルド情報が取得できないため無視")
            return

        # ボイスチャンネル以外は無視
        if not isinstance(message.channel, discord.VoiceChannel):
            debug_log(f"{message.channel.name} はボイスチャンネルではないため無視")
            return

        # 🔹 除外カテゴリー判定（DynamoDB ベース）
        if self.is_excluded(message.channel):
            cat_id = message.channel.category.id if message.channel.category else "N/A"
            debug_log(f"[SKIP] `{message.channel.name}` は除外カテゴリー (`{cat_id}`) に属するため無視")
            return

        # 転記先テキストチャンネルの取得 or 作成
        target_channel = await self.channel_manager.get_or_create_text_channel(guild, message.channel)
        if target_channel is None:
            return
        debug_log(f"転記先チャンネル: {target_channel.name} ({target_channel.id})")

        # メッセージ時刻（JST）
        message_time_jst = (
            message.created_at.replace(tzinfo=pytz.utc)
            .astimezone(jst)
            .strftime("%Y/%m/%d %H:%M:%S")
        )
        author_name = f"{message.author.display_name}   {message_time_jst}"

        if target_channel.guild.id == guild.id:
            # 同じサーバー内のアーカイブ：これまで通り元の添付URLで埋め込む
            await self._send_with_urls(message, target_channel, author_name)
        else:
            # 別サーバーのアーカイブ：添付をダウンロードしてアップロードし直す
            # （VCが0人になると元メッセージは削除され、元の添付URLは表示できなくなるため）
            await self._send_with_reupload(message, target_channel, author_name)

        # 他のコマンド処理へも回す
        await self.bot.process_commands(message)


async def setup(bot):
    await bot.add_cog(MessageHandlerCog(bot))
