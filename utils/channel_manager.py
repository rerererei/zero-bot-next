# utils/channel_manager.py

import discord
import datetime
import pytz
import asyncio
import re

from utils.helpers import normalize_text_channel_name
from config import debug_log
from data.guild_config_store import GuildConfigStore
from typing import Optional

jst = pytz.timezone("Asia/Tokyo")

config_store = GuildConfigStore()

# 万が一 guild_config に何も設定されていないときに使うデフォルトカテゴリ名
DEFAULT_CATEGORY_NAME = "インチャテキスト"


class ChannelManager:
    """ボイスチャンネルとテキストチャンネルの管理を統一（ギルド設定は DynamoDB から取得）"""
    def __init__(self, bot):
        self.bot = bot
        self.voice_text_mapping = {}
        self.max_cache_size = 10
        self.cleanup_interval = 3600  # 1時間ごとにキャッシュクリア
        self.cleanup_task = None  # タスクを保持

    # ==========================
    #   設定読み込み系
    # ==========================
    def _get_voice_text_category_from_config(self, guild: discord.Guild) -> Optional[discord.CategoryChannel]:
        """
        guild_config.logging から、ボイス用テキストチャンネルを置くカテゴリを取得する。
        優先順位:
          1) logging.voice_text_category_id
          2) logging.voice_text_category_name
          3) なし → None を返す（呼び出し元で fallback）
        """
        cfg = config_store.get_config(guild.id) or {}
        logging_cfg = cfg.get("logging") or {}

        raw_cat_id = logging_cfg.get("voice_text_category_id")
        cat_name = logging_cfg.get("voice_text_category_name")

        # 1) ID優先
        if raw_cat_id is not None:
            try:
                cat_id = int(raw_cat_id)
                ch = guild.get_channel(cat_id)
                if isinstance(ch, discord.CategoryChannel):
                    return ch
                else:
                    debug_log(f"[ChannelManager] voice_text_category_id={cat_id} は CategoryChannel ではありません")
            except (TypeError, ValueError):
                debug_log(f"[ChannelManager] voice_text_category_id が不正: {raw_cat_id}")

        # 2) 名前指定
        if cat_name:
            category = discord.utils.get(guild.categories, name=cat_name)
            if isinstance(category, discord.CategoryChannel):
                return category
            else:
                debug_log(f"[ChannelManager] voice_text_category_name='{cat_name}' のカテゴリが見つかりません")

        # 見つからなければ None（fallback は呼び出し側）
        return None

    # ==========================
    #   起動・停止時のタスク
    # ==========================
    async def start_cleanup_task(self):
        """Bot の起動時にキャッシュクリアタスクを開始"""
        if self.cleanup_task is None:
            self.cleanup_task = asyncio.create_task(self.cleanup_old_cache())
        # debug_log("[TASK] キャッシュクリアタスクを開始")

    async def stop_cleanup_task(self):
        """Bot のシャットダウン時にタスクを停止"""
        if self.cleanup_task:
            # debug_log("[CLEANUP TASK] タスクをキャンセルします")
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                debug_log("[CLEANUP TASK] タスクが正常にキャンセルされました")

    # ==========================
    #   メイン: チャンネル取得
    # ==========================
    async def get_or_create_text_channel(self, guild: discord.Guild, voice_channel: discord.VoiceChannel):
        """
        ボイスチャンネルに紐づくテキストチャンネルを取得または作成。
        - カテゴリは guild_config.logging.voice_text_category_* を優先
        - なければ voice_channel.category
        - それも無ければ DEFAULT_CATEGORY_NAME を新規作成
        - チャンネル名は `YYYYMMDD_正規化VC名`
        """
        # 1) カテゴリ候補を guild_config から取得
        category = self._get_voice_text_category_from_config(guild)

        # 2) 設定にカテゴリがない場合 → VC が属するカテゴリを使う
        # if category is None and voice_channel.category is not None:
        #     category = voice_channel.category

        # 3) それでも None なら、デフォルトカテゴリ名で作成
        if category is None:
            debug_log(f"[CREATE_CATEGORY] 設定 & VC からカテゴリが取得できないため `{DEFAULT_CATEGORY_NAME}` を新規作成します")
            category = discord.utils.get(guild.categories, name=DEFAULT_CATEGORY_NAME)
            if category is None:
                category = await guild.create_category(DEFAULT_CATEGORY_NAME)

        # ==============================
        #   ここから先は今までどおり
        # ==============================
        today_date = datetime.datetime.now(jst).strftime("%Y%m%d")
        expected_channel_name = f"{today_date}_{normalize_text_channel_name(voice_channel.name)}"

        # キャッシュ確認
        cached_channel = self.voice_text_mapping.get(voice_channel.id)
        if cached_channel:
            match = re.match(r"(\d{8})_", cached_channel.name)  # YYYYMMDD_ の形式
            cached_date = match.group(1) if match else None

            if cached_date == today_date:
                # debug_log(f"[CACHE_HIT] `{voice_channel.name}` → `{cached_channel.name}`")
                return cached_channel
            else:
                # debug_log(f"[CACHE_MISMATCH] キャッシュ日付 `{cached_date}` != `{today_date}` → キャッシュクリア")
                del self.voice_text_mapping[voice_channel.id]

        # 既存チャンネル検索
        target_channel = discord.utils.get(category.text_channels, name=expected_channel_name)

        if target_channel:
            # debug_log(f"[EXISTING_CHANNEL] 既存テキスト `{expected_channel_name}` を使用")
            self.voice_text_mapping[voice_channel.id] = target_channel
        else:
            # debug_log(f"[NEW_CHANNEL] テキストチャンネル `{expected_channel_name}` を新規作成")
            target_channel = await guild.create_text_channel(expected_channel_name, category=category)
            await target_channel.send(f"このテキストチャンネルは <#{voice_channel.id}> に紐づいています。")
            self.voice_text_mapping[voice_channel.id] = target_channel

        # キャッシュ上限管理
        if len(self.voice_text_mapping) > self.max_cache_size:
            removed_channel_id = next(iter(self.voice_text_mapping))
            removed_channel_name = self.voice_text_mapping[removed_channel_id].name
            del self.voice_text_mapping[removed_channel_id]
            # debug_log(f"[CACHE_CLEANUP] キャッシュ超過のため `{removed_channel_name}` を削除")

        # debug_log(f"[CACHE_STATE] {self._format_cache_state()}")
        return target_channel

    # ==========================
    #   キャッシュクリーンアップ
    # ==========================
    async def cleanup_old_cache(self):
        """定期的に古いキャッシュを削除するタスク"""
        try:
            while True:
                await asyncio.sleep(self.cleanup_interval)

                all_voice_channels = {vc.id for guild in self.bot.guilds for vc in guild.voice_channels}
                removed_channels = []

                for vc_id in list(self.voice_text_mapping.keys()):
                    if vc_id not in all_voice_channels:
                        del self.voice_text_mapping[vc_id]
                        removed_channels.append(vc_id)

                if removed_channels:
                    debug_log(f"[CACHE_CLEANUP] {len(removed_channels)} 件の古いキャッシュを削除しました")

                if len(self.voice_text_mapping) > self.max_cache_size:
                    excess = len(self.voice_text_mapping) - self.max_cache_size
                    for _ in range(excess):
                        removed_channel_id = next(iter(self.voice_text_mapping))
                        removed_channel_name = self.voice_text_mapping[removed_channel_id].name
                        del self.voice_text_mapping[removed_channel_id]
                    # debug_log(f"[CACHE_CLEANUP] 上限超過のため {excess} 件を削除しました")

                # debug_log(f"[CACHE_STATE] {self._format_cache_state()}")

        except asyncio.CancelledError:
            debug_log("[CLEANUP TASK] Bot の終了を検知、タスクを停止します")

    def _format_cache_state(self):
        """キャッシュの現在の状態をフォーマット"""
        if not self.voice_text_mapping:
            return "空"
        return ", ".join([f"{vc_id}: {channel.name}" for vc_id, channel in self.voice_text_mapping.items()])
