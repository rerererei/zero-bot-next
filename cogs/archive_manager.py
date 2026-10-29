import discord
from discord import app_commands
from discord.ext import commands
import datetime
import pytz
import re
from typing import List, Optional

from config import debug_log
from data.guild_config_store import GuildConfigStore
from utils.channel_manager import ARCHIVE_CATEGORY_PATTERN, DEFAULT_CATEGORY_NAME

jst = pytz.timezone("Asia/Tokyo")

# ギルド設定の読み取り用（DynamoDB）
guild_config_store = GuildConfigStore()


class ArchiveManagerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _find_archive_category(self, guild: discord.Guild, archive_cfg: dict) -> Optional[discord.CategoryChannel]:
        """
        config.archive の設定から、guild 内のアーカイブ用カテゴリーを探す。
        優先順位:
          1) config.archive.category_id
          2) config.archive.category_name
          3) どちらも未設定なら、ChannelManager が自動作成する DEFAULT_CATEGORY_NAME
        """
        cat_id_raw = archive_cfg.get("category_id")
        cat_name = archive_cfg.get("category_name")

        # 1) ID 指定があれば優先して使う
        if cat_id_raw:
            try:
                cat_id = int(cat_id_raw)
                ch = guild.get_channel(cat_id)
                if isinstance(ch, discord.CategoryChannel):
                    return ch
            except (TypeError, ValueError):
                debug_log(f"[ARCHIVE] invalid category_id in config: {cat_id_raw}")

        # 2) 名前指定があれば名前から検索
        if cat_name:
            category = discord.utils.get(guild.categories, name=cat_name)
            if isinstance(category, discord.CategoryChannel):
                return category

        # 3) 設定が無いサーバーは自動作成されたカテゴリー
        if not cat_id_raw and not cat_name:
            return discord.utils.get(guild.categories, name=DEFAULT_CATEGORY_NAME)

        # 見つからない
        return None

    @staticmethod
    def _archive_server_id(archive_cfg: dict) -> Optional[int]:
        """config.archive.server_id（アーカイブ用の別サーバーID）。未設定・不正なら None"""
        raw_id = archive_cfg.get("server_id")
        if not raw_id:
            return None
        try:
            return int(raw_id)
        except (TypeError, ValueError):
            debug_log(f"[ARCHIVE] invalid server_id in config: {raw_id}")
            return None

    def _get_archive_categories(self, guild: discord.Guild) -> List[discord.CategoryChannel]:
        """
        コマンドを実行したサーバー内にあるアーカイブ用カテゴリーを返す。
        - 実行サーバー自身の archive.category_id / category_name のカテゴリー
          （archive.server_id を設定済みでも、設定前のアーカイブが残っているので対象にする）
        - 他サーバーの設定で archive.server_id が実行サーバーを指していれば（＝アーカイブ用サーバー）、
          `yyyymm_nn` 形式のカテゴリーをすべて対象にする
        """
        categories: List[discord.CategoryChannel] = []

        own_archive_cfg = (guild_config_store.get_config(guild.id) or {}).get("archive") or {}
        category = self._find_archive_category(guild, own_archive_cfg)
        if category is not None:
            categories.append(category)

        if self._is_archive_server(guild):
            categories.extend(c for c in guild.categories if ARCHIVE_CATEGORY_PATTERN.match(c.name))

        # 同じカテゴリーが重複しても1回だけ
        return list({c.id: c for c in categories}.values())

    def _is_archive_server(self, guild: discord.Guild) -> bool:
        """他サーバーの archive.server_id がこのサーバーを指しているか"""
        for source_guild in self.bot.guilds:
            if source_guild.id == guild.id:
                continue
            archive_cfg = (guild_config_store.get_config(source_guild.id) or {}).get("archive") or {}
            if self._archive_server_id(archive_cfg) == guild.id:
                return True
        return False

    @app_commands.command(
        name="manage_comment",
        description="管理者用コマンド",
    )
    @app_commands.describe(
        date="（yyyymmdd）"
    )
    @app_commands.default_permissions(administrator=True)  # ★ 管理者権限が必要
    @app_commands.checks.has_permissions(administrator=True)  # ★ 念のため実行時チェックも
    @app_commands.guild_only()  # DMで使えないように（任意だけどおすすめ）
    async def manage_comment(self, interaction: discord.Interaction, date: str):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "エラー: サーバー情報が取得できません。",
                ephemeral=True,
            )
            return

        # 日付フォーマットチェック
        if not re.match(r"^\d{8}$", date):
            await interaction.response.send_message(
                "❌ 無効な日付フォーマットです。`yyyymmdd` 形式で指定してください。",
                ephemeral=True,
            )
            return

        try:
            threshold_date = datetime.datetime.strptime(date, "%Y%m%d").replace(tzinfo=jst)
        except ValueError:
            await interaction.response.send_message(
                "❌ 無効な日付です。正しい `yyyymmdd` 形式で指定してください。",
                ephemeral=True,
            )
            return

        categories = self._get_archive_categories(guild)
        if not categories:
            await interaction.response.send_message(
                "⚠ このサーバーにはアーカイブ用カテゴリーがありません。\n"
                "アーカイブを置いているサーバーで実行するか、"
                "`config.archive.category_id` または `category_name` の設定を確認してください。",
                ephemeral=True,
            )
            return

        debug_log(
            f"[DELETE ARCHIVE] `{date}` 以前のアーカイブチャンネルを削除します "
            f"(categories={[c.name for c in categories]})"
        )

        # カテゴリー配下のチャンネル名から yyyymmdd プレフィックスを見て削除対象を抽出
        channels_to_delete = []
        for channel in [ch for category in categories for ch in category.text_channels]:
            match = re.match(r"^(\d{8})_", channel.name)
            if match:
                channel_date_str = match.group(1)
                try:
                    channel_date = datetime.datetime.strptime(channel_date_str, "%Y%m%d").replace(tzinfo=jst)
                    if channel_date <= threshold_date:
                        channels_to_delete.append(channel)
                except ValueError:
                    continue

        if not channels_to_delete:
            await interaction.response.send_message(
                f"✅ `{date}` 以前の削除対象チャンネルはありませんでした。",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        confirm_msg = await interaction.followup.send(
            f"⚠ `{date}` 以前の `{len(channels_to_delete)}` 件のアーカイブチャンネルを削除します。実行してもよろしいですか？",
            view=DeleteConfirmView(self.bot, interaction, channels_to_delete),
        )
        self.bot.confirmation_messages[interaction.id] = confirm_msg


class DeleteConfirmView(discord.ui.View):
    def __init__(self, bot, interaction, channels_to_delete):
        super().__init__(timeout=30)
        self.bot = bot
        self.interaction = interaction
        self.channels_to_delete = channels_to_delete

    async def delete_original_message(self):
        """確認メッセージを削除"""
        confirm_msg = self.bot.confirmation_messages.pop(self.interaction.id, None)
        if confirm_msg:
            try:
                await confirm_msg.delete()
            except discord.NotFound:
                pass

    @discord.ui.button(label="削除", style=discord.ButtonStyle.danger)
    async def confirm_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()

        # 実行者チェック
        if interaction.user != self.interaction.user:
            return  # ←何も返さない（結果メッセージなし）

        # チャンネル削除処理だけ実行
        deleted_ids = set()
        for channel in self.channels_to_delete:
            try:
                await channel.delete()
                deleted_ids.add(channel.id)
            except Exception:
                pass  # エラーも無視（結果は送らない）

        # アーカイブ用サーバーの `yyyymm_nn` カテゴリーが空になったら消す
        categories = {ch.category.id: ch.category for ch in self.channels_to_delete if ch.category}
        for category in categories.values():
            if not ARCHIVE_CATEGORY_PATTERN.match(category.name):
                continue
            if any(ch.id not in deleted_ids for ch in category.channels):
                continue
            try:
                await category.delete()
            except Exception:
                pass

        # 確認メッセージだけ消す
        await self.delete_original_message()

        # ★ followup/send はしない
        return

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()

        if interaction.user != self.interaction.user:
            return  # 返さない

        # メッセージ削除だけする
        await self.delete_original_message()
        return

async def setup(bot):
    bot.confirmation_messages = {}  # ✅ メッセージ管理用辞書を追加
    await bot.add_cog(ArchiveManagerCog(bot))
