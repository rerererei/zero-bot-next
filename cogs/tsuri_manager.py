from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Dict, Optional

import discord
from discord import app_commands
from discord.ext import commands


# ========================================
# 設定
# ========================================

DATA_FILE = Path("data/tsuri_settings.json")

# 連続投稿時に何度も再投稿しないための待機時間
REFRESH_DELAY_SECONDS = 0.8

# Embedの左側カラー
TSURI_COLOR = discord.Color.from_rgb(128, 128, 128)

SUPPORTED_CHANNEL_TYPES = (
    discord.TextChannel,
    discord.VoiceChannel,
)


# ========================================
# 入力モーダル
# ========================================

class TsuriModal(discord.ui.Modal):
    def __init__(
        self,
        cog: "TsuriManager",
        channel: discord.abc.Messageable,
        current_title: str = "",
        current_body: str = "",
        is_edit: bool = False,
    ):
        modal_title = (
            "吊り下げメッセージを編集"
            if is_edit
            else "吊り下げメッセージを設定"
        )

        super().__init__(
            title=modal_title,
            timeout=300,
        )

        self.cog = cog
        self.channel = channel
        self.is_edit = is_edit

        self.title_input = discord.ui.TextInput(
            label="タイトル",
            placeholder="例：このチャンネルについて",
            default=current_title,
            required=True,
            max_length=256,
        )

        self.body_input = discord.ui.TextInput(
            label="本文",
            placeholder="チャンネルの案内やルールを入力してください。",
            default=current_body,
            required=True,
            max_length=4000,
            style=discord.TextStyle.paragraph,
        )

        self.add_item(self.title_input)
        self.add_item(self.body_input)

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):
        await interaction.response.defer(ephemeral=True)

        title = self.title_input.value.strip()
        body = self.body_input.value.strip()

        if not title:
            await interaction.followup.send(
                "❌ タイトルを入力してください。",
                ephemeral=True,
            )
            return

        if not body:
            await interaction.followup.send(
                "❌ 本文を入力してください。",
                ephemeral=True,
            )
            return

        try:
            await self.cog.publish_tsuri(
                channel=self.channel,
                title=title,
                body=body,
            )

            action = "更新" if self.is_edit else "設定"

            await interaction.followup.send(
                f"✅ 吊り下げメッセージを{action}しました。",
                ephemeral=True,
            )

        except discord.Forbidden:
            await interaction.followup.send(
                "❌ ZERO-Botにメッセージを送信する権限がありません。",
                ephemeral=True,
            )

        except discord.HTTPException as error:
            print(
                "❌ 吊り下げメッセージの送信に失敗しました: "
                f"channel={getattr(self.channel, 'id', 'unknown')}, "
                f"error={error}"
            )

            await interaction.followup.send(
                "❌ 吊り下げメッセージの送信に失敗しました。",
                ephemeral=True,
            )

        except Exception as error:
            print(
                "❌ 吊り下げ機能で予期しないエラーが発生しました: "
                f"channel={getattr(self.channel, 'id', 'unknown')}, "
                f"error={error}"
            )

            await interaction.followup.send(
                "❌ 吊り下げメッセージの処理中にエラーが発生しました。",
                ephemeral=True,
            )


# ========================================
# Cog本体
# ========================================

class TsuriManager(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # チャンネル単位の設定
        self.settings: Dict[str, dict] = self._load_settings()

        # チャンネル単位の排他制御
        self.channel_locks: Dict[int, asyncio.Lock] = {}

        # JSON保存時の排他制御
        self.file_lock = asyncio.Lock()

        # 再投稿待機タスク
        self.refresh_tasks: Dict[int, asyncio.Task] = {}

    def cog_unload(self):
        """Cogアンロード時に待機中タスクを終了する"""

        for task in self.refresh_tasks.values():
            if not task.done():
                task.cancel()

        self.refresh_tasks.clear()

    # ========================================
    # JSON操作
    # ========================================

    def _load_settings(self) -> Dict[str, dict]:
        """JSONから吊り下げ設定を読み込む"""

        if not DATA_FILE.exists():
            return {}

        try:
            text = DATA_FILE.read_text(encoding="utf-8")
            data = json.loads(text)

            if isinstance(data, dict):
                return data

        except json.JSONDecodeError as error:
            print(
                "❌ 吊り下げ設定JSONの形式が不正です: "
                f"file={DATA_FILE}, error={error}"
            )

        except OSError as error:
            print(
                "❌ 吊り下げ設定JSONを読み込めませんでした: "
                f"file={DATA_FILE}, error={error}"
            )

        return {}

    @staticmethod
    def _write_settings_file(serialized: str):
        """JSONファイルへアトミックに保存する"""

        DATA_FILE.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        temporary_file = DATA_FILE.with_suffix(".tmp")

        temporary_file.write_text(
            serialized,
            encoding="utf-8",
        )

        temporary_file.replace(DATA_FILE)

    async def _update_setting(
        self,
        channel_id: int,
        setting: Optional[dict],
    ):
        """設定を更新してJSONへ保存する"""

        async with self.file_lock:
            key = str(channel_id)

            if setting is None:
                self.settings.pop(key, None)
            else:
                self.settings[key] = setting

            serialized = json.dumps(
                self.settings,
                ensure_ascii=False,
                indent=2,
            )

            await asyncio.to_thread(
                self._write_settings_file,
                serialized,
            )

    def get_setting(
        self,
        channel_id: int,
    ) -> Optional[dict]:
        """チャンネルの設定を取得する"""

        setting = self.settings.get(str(channel_id))

        if setting is None:
            return None

        return dict(setting)

    # ========================================
    # 権限・チャンネル確認
    # ========================================

    async def validate_command(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        """コマンド実行条件を確認する"""

        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ サーバー内で使用してください。",
                ephemeral=True,
            )
            return False

        channel = interaction.channel

        if not isinstance(channel, SUPPORTED_CHANNEL_TYPES):
            await interaction.response.send_message(
                "❌ テキストチャンネル、またはVCのインチャで使用してください。",
                ephemeral=True,
            )
            return False

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "❌ メンバー情報を取得できませんでした。",
                ephemeral=True,
            )
            return False

        permissions = channel.permissions_for(interaction.user)

        if not (
            permissions.administrator
            or permissions.manage_messages
            or permissions.manage_channels
        ):
            await interaction.response.send_message(
                "❌ この機能を使用するには、"
                "「メッセージの管理」または「チャンネルの管理」権限が必要です。",
                ephemeral=True,
            )
            return False

        return True

    def get_channel_lock(
        self,
        channel_id: int,
    ) -> asyncio.Lock:
        """チャンネル単位のLockを取得する"""

        lock = self.channel_locks.get(channel_id)

        if lock is None:
            lock = asyncio.Lock()
            self.channel_locks[channel_id] = lock

        return lock

    # ========================================
    # Embed作成
    # ========================================

    @staticmethod
    def create_tsuri_embed(
        title: str,
        body: str,
    ) -> discord.Embed:
        """吊り下げ用Embedを作成する"""

        return discord.Embed(
            title=title,
            description=body,
            color=TSURI_COLOR,
        )

    # ========================================
    # メッセージ操作
    # ========================================

    async def delete_message(
        self,
        channel: discord.abc.Messageable,
        message_id: Optional[int],
    ):
        """指定したメッセージを削除する"""

        if not message_id:
            return

        try:
            message = await channel.fetch_message(message_id)
            await message.delete()

        except discord.NotFound:
            # 既に削除済みなら何もしない
            pass

        except discord.Forbidden:
            print(
                "❌ 吊り下げメッセージを削除する権限がありません: "
                f"channel={getattr(channel, 'id', 'unknown')}, "
                f"message={message_id}"
            )

        except discord.HTTPException as error:
            print(
                "❌ 吊り下げメッセージの削除に失敗しました: "
                f"channel={getattr(channel, 'id', 'unknown')}, "
                f"message={message_id}, "
                f"error={error}"
            )

    async def _publish_locked(
        self,
        channel: discord.abc.Messageable,
        title: str,
        body: str,
        current_setting: Optional[dict],
    ) -> discord.Message:
        """
        新しい吊り下げメッセージを投稿し、
        設定保存後に古いメッセージを削除する。
        """

        embed = self.create_tsuri_embed(
            title=title,
            body=body,
        )

        new_message = await channel.send(embed=embed)

        channel_id = channel.id
        guild_id = channel.guild.id

        new_setting = {
            "guild_id": guild_id,
            "channel_id": channel_id,
            "message_id": new_message.id,
            "title": title,
            "body": body,
        }

        try:
            await self._update_setting(
                channel_id=channel_id,
                setting=new_setting,
            )

        except Exception:
            # 保存できなかった場合は新しい投稿を残さない
            try:
                await new_message.delete()
            except discord.HTTPException:
                pass

            raise

        old_message_id = None

        if current_setting is not None:
            old_message_id = current_setting.get("message_id")

        if old_message_id != new_message.id:
            await self.delete_message(
                channel=channel,
                message_id=old_message_id,
            )

        return new_message

    async def publish_tsuri(
        self,
        channel: discord.abc.Messageable,
        title: str,
        body: str,
    ) -> discord.Message:
        """吊り下げメッセージを新規作成・更新する"""

        lock = self.get_channel_lock(channel.id)

        async with lock:
            current_setting = self.get_setting(channel.id)

            return await self._publish_locked(
                channel=channel,
                title=title,
                body=body,
                current_setting=current_setting,
            )

    async def refresh_tsuri(
        self,
        channel: discord.abc.Messageable,
    ):
        """現在の吊り下げメッセージを最下部へ再投稿する"""

        lock = self.get_channel_lock(channel.id)

        async with lock:
            current_setting = self.get_setting(channel.id)

            # 待機中に解除された場合
            if current_setting is None:
                return

            await self._publish_locked(
                channel=channel,
                title=current_setting["title"],
                body=current_setting["body"],
                current_setting=current_setting,
            )

    async def remove_tsuri(
        self,
        channel: discord.abc.Messageable,
    ) -> bool:
        """吊り下げ設定を解除する"""

        channel_id = channel.id

        pending_task = self.refresh_tasks.pop(
            channel_id,
            None,
        )

        if pending_task is not None and not pending_task.done():
            pending_task.cancel()

        lock = self.get_channel_lock(channel_id)

        async with lock:
            current_setting = self.get_setting(channel_id)

            if current_setting is None:
                return False

            # 先に設定を解除して再投稿を防止
            await self._update_setting(
                channel_id=channel_id,
                setting=None,
            )

            await self.delete_message(
                channel=channel,
                message_id=current_setting.get("message_id"),
            )

            return True

    # ========================================
    # 再投稿制御
    # ========================================

    async def refresh_after_delay(
        self,
        channel: discord.abc.Messageable,
    ):
        """連続投稿をまとめてから再投稿する"""

        try:
            await asyncio.sleep(REFRESH_DELAY_SECONDS)
            await self.refresh_tsuri(channel)

        except asyncio.CancelledError:
            raise

        except Exception as error:
            print(
                "❌ 吊り下げメッセージの再投稿に失敗しました: "
                f"channel={getattr(channel, 'id', 'unknown')}, "
                f"error={error}"
            )

    def clear_refresh_task(
        self,
        channel_id: int,
        finished_task: asyncio.Task,
    ):
        """完了した再投稿タスクを辞書から削除する"""

        current_task = self.refresh_tasks.get(channel_id)

        if current_task is finished_task:
            self.refresh_tasks.pop(channel_id, None)

    @commands.Cog.listener()
    async def on_message(
        self,
        message: discord.Message,
    ):
        # DMは対象外
        if message.guild is None:
            return

        # ZERO-Bot自身の投稿では再投稿しない
        if (
            self.bot.user is not None
            and message.author.id == self.bot.user.id
        ):
            return

        channel = message.channel

        if not isinstance(channel, SUPPORTED_CHANNEL_TYPES):
            return

        setting = self.get_setting(channel.id)

        if setting is None:
            return

        # 現在の吊り下げメッセージ自身なら無視
        if message.id == setting.get("message_id"):
            return

        # 連続投稿中なら、前の待機タスクをキャンセル
        old_task = self.refresh_tasks.get(channel.id)

        if old_task is not None and not old_task.done():
            old_task.cancel()

        new_task = asyncio.create_task(
            self.refresh_after_delay(channel)
        )

        self.refresh_tasks[channel.id] = new_task

        new_task.add_done_callback(
            lambda task, channel_id=channel.id:
            self.clear_refresh_task(channel_id, task)
        )

    # ========================================
    # スラッシュコマンド
    # ========================================

    @app_commands.command(
        name="tsuri_set",
        description="このチャンネルに吊り下げメッセージを設定します",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def tsuri_set(
        self,
        interaction: discord.Interaction,
    ):
        if not await self.validate_command(interaction):
            return

        channel = interaction.channel

        current_setting = self.get_setting(channel.id)

        if current_setting is not None:
            await interaction.response.send_message(
                "⚠️ このチャンネルには既に吊り下げが設定されています。\n"
                "内容を変更する場合は `/tsuri_edit` を使用してください。",
                ephemeral=True,
            )
            return

        modal = TsuriModal(
            cog=self,
            channel=channel,
            is_edit=False,
        )

        await interaction.response.send_modal(modal)

    @app_commands.command(
        name="tsuri_edit",
        description="このチャンネルの吊り下げメッセージを編集します",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def tsuri_edit(
        self,
        interaction: discord.Interaction,
    ):
        if not await self.validate_command(interaction):
            return

        channel = interaction.channel

        current_setting = self.get_setting(channel.id)

        if current_setting is None:
            await interaction.response.send_message(
                "⚠️ このチャンネルには吊り下げが設定されていません。\n"
                "先に `/tsuri_set` を使用してください。",
                ephemeral=True,
            )
            return

        modal = TsuriModal(
            cog=self,
            channel=channel,
            current_title=current_setting.get("title", ""),
            current_body=current_setting.get("body", ""),
            is_edit=True,
        )

        await interaction.response.send_modal(modal)

    @app_commands.command(
        name="tsuri_off",
        description="このチャンネルの吊り下げメッセージを解除します",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def tsuri_off(
        self,
        interaction: discord.Interaction,
    ):
        if not await self.validate_command(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        channel = interaction.channel

        try:
            removed = await self.remove_tsuri(channel)

            if not removed:
                await interaction.followup.send(
                    "⚠️ このチャンネルには吊り下げが設定されていません。",
                    ephemeral=True,
                )
                return

            await interaction.followup.send(
                "✅ 吊り下げメッセージを解除しました。",
                ephemeral=True,
            )

        except Exception as error:
            print(
                "❌ 吊り下げ解除に失敗しました: "
                f"channel={channel.id}, "
                f"error={error}"
            )

            await interaction.followup.send(
                "❌ 吊り下げメッセージの解除に失敗しました。",
                ephemeral=True,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(TsuriManager(bot))