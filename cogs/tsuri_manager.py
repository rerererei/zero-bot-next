from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Dict, List, Optional
from uuid import uuid4

import discord
from discord import app_commands
from discord.ext import commands


DATA_FILE = Path("data/tsuri_settings.json")
IMAGE_DIR = Path("data/tsuri_images")

REFRESH_DELAY_SECONDS = 0.8
MAX_IMAGE_BYTES = 8 * 1024 * 1024

TSURI_COLOR = discord.Color.from_rgb(128, 128, 128)

SUPPORTED_CHANNEL_TYPES = (
    discord.TextChannel,
    discord.VoiceChannel,
)

SUPPORTED_IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
}


class TsuriModal(discord.ui.Modal):
    def __init__(
        self,
        cog: "TsuriManager",
        channel,
        current_text: str = "",
        image_attachment: Optional[discord.Attachment] = None,
        remove_image: bool = False,
        is_edit: bool = False,
    ):
        super().__init__(
            title=(
                "吊り下げメッセージを編集"
                if is_edit
                else "吊り下げメッセージを設定"
            ),
            timeout=300,
        )

        self.cog = cog
        self.channel = channel
        self.image_attachment = image_attachment
        self.remove_image = remove_image
        self.is_edit = is_edit

        self.text_input = discord.ui.TextInput(
            label="テキスト（空欄可）",
            placeholder=(
                "画像だけを表示する場合は、"
                "この欄を空欄のまま送信してください。"
            ),
            default=current_text[:4000],
            required=False,
            max_length=4000,
            style=discord.TextStyle.paragraph,
        )

        self.add_item(self.text_input)

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):
        await interaction.response.defer(ephemeral=True)

        text = self.text_input.value.strip()

        current_setting = self.cog.get_setting(
            self.channel.id
        )

        old_image_path: Optional[str] = None

        if current_setting is not None:
            old_image_path = current_setting.get(
                "image_path"
            )

        # 現在の画像を維持するのが初期状態
        image_path = old_image_path
        newly_saved_image_path: Optional[str] = None

        try:
            # 画像削除が指定されている場合
            if self.remove_image:
                image_path = None

            # 新しい画像が指定されている場合
            if self.image_attachment is not None:
                newly_saved_image_path = (
                    await self.cog.save_image_attachment(
                        channel_id=self.channel.id,
                        attachment=self.image_attachment,
                    )
                )

                image_path = newly_saved_image_path

            # 画像もテキストもない場合は設定不可
            if not image_path and not text:
                if newly_saved_image_path:
                    self.cog.delete_local_image(
                        newly_saved_image_path
                    )

                await interaction.followup.send(
                    "❌ 画像またはテキストの"
                    "どちらか一方は設定してください。",
                    ephemeral=True,
                )
                return

            await self.cog.publish_tsuri(
                channel=self.channel,
                text=text,
                image_path=image_path,
            )

            # 新しい設定の保存後に古い画像を削除
            if (
                old_image_path
                and old_image_path != image_path
            ):
                self.cog.delete_local_image(
                    old_image_path
                )

            action = "更新" if self.is_edit else "設定"

            if image_path and text:
                pattern = "画像＋テキスト"
            elif image_path:
                pattern = "画像のみ"
            else:
                pattern = "テキストのみ"

            await interaction.followup.send(
                f"✅ 吊り下げを{action}しました。\n"
                f"表示形式：**{pattern}**",
                ephemeral=True,
            )

        except Exception as error:
            # 新画像を保存したあとに失敗した場合は削除
            if newly_saved_image_path:
                self.cog.delete_local_image(
                    newly_saved_image_path
                )

            print(
                "❌ 吊り下げ設定に失敗しました: "
                f"channel={self.channel.id}, "
                f"error={error}"
            )

            await interaction.followup.send(
                "❌ 吊り下げの設定に失敗しました。",
                ephemeral=True,
            )


class TsuriManager(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.settings: Dict[str, dict] = (
            self._load_settings()
        )

        self.channel_locks: Dict[
            int,
            asyncio.Lock,
        ] = {}

        self.refresh_tasks: Dict[
            int,
            asyncio.Task,
        ] = {}

        self.file_lock = asyncio.Lock()

    def cog_unload(self):
        for task in self.refresh_tasks.values():
            if not task.done():
                task.cancel()

        self.refresh_tasks.clear()

    # ========================================
    # JSON
    # ========================================

    def _load_settings(self) -> Dict[str, dict]:
        if not DATA_FILE.exists():
            return {}

        try:
            data = json.loads(
                DATA_FILE.read_text(
                    encoding="utf-8"
                )
            )

            if isinstance(data, dict):
                return data

        except Exception as error:
            print(
                "❌ 吊り下げ設定の読込失敗: "
                f"error={error}"
            )

        return {}

    @staticmethod
    def _write_settings_file(
        serialized: str,
    ):
        DATA_FILE.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        temporary_file = DATA_FILE.with_suffix(
            ".tmp"
        )

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
        setting = self.settings.get(
            str(channel_id)
        )

        return dict(setting) if setting else None

    # ========================================
    # 画像
    # ========================================

    @staticmethod
    def validate_image(
        attachment: discord.Attachment,
    ) -> Optional[str]:
        extension = Path(
            attachment.filename
        ).suffix.lower()

        if extension not in SUPPORTED_IMAGE_EXTENSIONS:
            return (
                "PNG・JPG・JPEG・WEBP・GIF形式の"
                "画像を指定してください。"
            )

        if attachment.size > MAX_IMAGE_BYTES:
            return "画像サイズは8MB以下にしてください。"

        return None

    async def save_image_attachment(
        self,
        channel_id: int,
        attachment: discord.Attachment,
    ) -> str:
        error_message = self.validate_image(
            attachment
        )

        if error_message:
            raise ValueError(error_message)

        IMAGE_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        extension = Path(
            attachment.filename
        ).suffix.lower()

        image_path = IMAGE_DIR / (
            f"{channel_id}_{uuid4().hex}{extension}"
        )

        await attachment.save(image_path)

        return str(image_path)

    @staticmethod
    def delete_local_image(
        image_path: Optional[str],
    ):
        if not image_path:
            return

        try:
            Path(image_path).unlink(
                missing_ok=True
            )
        except OSError as error:
            print(
                "❌ 吊り下げ画像の削除失敗: "
                f"path={image_path}, "
                f"error={error}"
            )

    # ========================================
    # メッセージ
    # ========================================

    @staticmethod
    def get_message_ids(
        setting: Optional[dict],
    ) -> List[int]:
        if not setting:
            return []

        message_ids = setting.get(
            "message_ids"
        )

        if isinstance(message_ids, list):
            return [
                int(message_id)
                for message_id in message_ids
            ]

        # 旧形式との互換
        old_message_id = setting.get(
            "message_id"
        )

        if old_message_id:
            return [int(old_message_id)]

        return []

    async def delete_messages(
        self,
        channel,
        message_ids: List[int],
    ):
        for message_id in message_ids:
            try:
                message = await channel.fetch_message(
                    message_id
                )

                await message.delete()

            except discord.NotFound:
                pass

            except discord.HTTPException as error:
                print(
                    "❌ 吊り下げ投稿の削除失敗: "
                    f"message={message_id}, "
                    f"error={error}"
                )

    def get_channel_lock(
        self,
        channel_id: int,
    ) -> asyncio.Lock:
        if channel_id not in self.channel_locks:
            self.channel_locks[channel_id] = (
                asyncio.Lock()
            )

        return self.channel_locks[channel_id]

    async def _publish_locked(
        self,
        channel,
        text: str,
        image_path: Optional[str],
        current_setting: Optional[dict],
    ):
        """
        古い吊り下げを先に削除してから、
        新しい吊り下げを投稿する。
        """

        new_messages: List[discord.Message] = []

        # 現在登録されている古い吊り下げのメッセージID
        old_message_ids = self.get_message_ids(
            current_setting
        )

        # ========================================
        # 1. 古い吊り下げを先に削除
        # ========================================

        await self.delete_messages(
            channel=channel,
            message_ids=old_message_ids,
        )

        # 削除済みのIDを設定から一旦外しておく
        temporary_setting = {
            "guild_id": channel.guild.id,
            "channel_id": channel.id,
            "message_ids": [],
            "text": text,
            "image_path": image_path,
        }

        await self._update_setting(
            channel_id=channel.id,
            setting=temporary_setting,
        )

        try:
            # ========================================
            # 2. 画像を投稿
            # ========================================

            if image_path:
                path = Path(image_path)

                if not path.exists():
                    raise FileNotFoundError(
                        f"画像が見つかりません: {image_path}"
                    )

                image_message = await channel.send(
                    file=discord.File(
                        path,
                        filename=path.name,
                    )
                )

                new_messages.append(
                    image_message
                )

            # ========================================
            # 3. テキストを投稿
            # ========================================

            if text:
                embed = discord.Embed(
                    description=text,
                    color=TSURI_COLOR,
                )

                text_message = await channel.send(
                    embed=embed
                )

                new_messages.append(
                    text_message
                )

            # 念のため、両方空ならエラー
            if not new_messages:
                raise ValueError(
                    "画像またはテキストのどちらかが必要です。"
                )

            # ========================================
            # 4. 新しいメッセージIDを保存
            # ========================================

            new_setting = {
                "guild_id": channel.guild.id,
                "channel_id": channel.id,
                "message_ids": [
                    message.id
                    for message in new_messages
                ],
                "text": text,
                "image_path": image_path,
            }

            await self._update_setting(
                channel_id=channel.id,
                setting=new_setting,
            )

        except Exception:
            # 新しい投稿が途中まで成功していた場合は削除
            for message in new_messages:
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass

            # 内容は残し、メッセージIDだけ空の状態にしておく
            # 次回の投稿時に再び吊り下げ作成を試行できる
            await self._update_setting(
                channel_id=channel.id,
                setting=temporary_setting,
            )

            raise
        
    async def publish_tsuri(
        self,
        channel,
        text: str,
        image_path: Optional[str],
    ):
        lock = self.get_channel_lock(
            channel.id
        )

        async with lock:
            current_setting = self.get_setting(
                channel.id
            )

            await self._publish_locked(
                channel=channel,
                text=text,
                image_path=image_path,
                current_setting=current_setting,
            )

    async def refresh_tsuri(
        self,
        channel,
    ):
        lock = self.get_channel_lock(
            channel.id
        )

        async with lock:
            current_setting = self.get_setting(
                channel.id
            )

            if current_setting is None:
                return

            text = current_setting.get(
                "text",
                "",
            )

            # 旧JSONのtitle/bodyにも対応
            if not text:
                title = current_setting.get(
                    "title",
                    "",
                )

                body = current_setting.get(
                    "body",
                    "",
                )

                if title and body:
                    text = f"## {title}\n{body}"
                elif title:
                    text = f"## {title}"
                else:
                    text = body

            await self._publish_locked(
                channel=channel,
                text=text,
                image_path=current_setting.get(
                    "image_path"
                ),
                current_setting=current_setting,
            )

    # ========================================
    # 再投稿
    # ========================================

    async def refresh_after_delay(
        self,
        channel,
    ):
        try:
            await asyncio.sleep(
                REFRESH_DELAY_SECONDS
            )

            await self.refresh_tsuri(
                channel
            )

        except asyncio.CancelledError:
            raise

        except Exception as error:
            print(
                "❌ 吊り下げ再投稿失敗: "
                f"channel={channel.id}, "
                f"error={error}"
            )

    @commands.Cog.listener()
    async def on_message(
        self,
        message: discord.Message,
    ):
        if message.guild is None:
            return

        # ZERO-Bot自身の投稿では動かさない
        if (
            self.bot.user is not None
            and message.author.id == self.bot.user.id
        ):
            return

        if not isinstance(
            message.channel,
            SUPPORTED_CHANNEL_TYPES,
        ):
            return

        if not self.get_setting(
            message.channel.id
        ):
            return

        old_task = self.refresh_tasks.get(
            message.channel.id
        )

        if old_task and not old_task.done():
            old_task.cancel()

        task = asyncio.create_task(
            self.refresh_after_delay(
                message.channel
            )
        )

        self.refresh_tasks[
            message.channel.id
        ] = task

    # ========================================
    # 権限
    # ========================================

    async def validate_command(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ サーバー内で使用してください。",
                ephemeral=True,
            )
            return False

        if not isinstance(
            interaction.channel,
            SUPPORTED_CHANNEL_TYPES,
        ):
            await interaction.response.send_message(
                "❌ テキストチャンネルまたは"
                "VCのインチャで使用してください。",
                ephemeral=True,
            )
            return False

        if not interaction.permissions.administrator:
            await interaction.response.send_message(
                "❌ このコマンドは管理者専用です。",
                ephemeral=True,
            )
            return False

        return True

    # ========================================
    # コマンド
    # ========================================

    @app_commands.command(
        name="tsuri_set",
        description="このチャンネルに吊り下げを設定します",
    )
    @app_commands.describe(
        image="吊り下げに使用する画像（任意）",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(
        administrator=True
    )
    @app_commands.checks.has_permissions(
        administrator=True
    )
    async def tsuri_set(
        self,
        interaction: discord.Interaction,
        image: Optional[discord.Attachment] = None,
    ):
        if not await self.validate_command(
            interaction
        ):
            return

        if self.get_setting(
            interaction.channel.id
        ):
            await interaction.response.send_message(
                "⚠️ 既に設定されています。\n"
                "`/tsuri_edit`を使用してください。",
                ephemeral=True,
            )
            return

        if image:
            error_message = self.validate_image(
                image
            )

            if error_message:
                await interaction.response.send_message(
                    f"❌ {error_message}",
                    ephemeral=True,
                )
                return

        await interaction.response.send_modal(
            TsuriModal(
                cog=self,
                channel=interaction.channel,
                image_attachment=image,
                is_edit=False,
            )
        )

    @app_commands.command(
        name="tsuri_edit",
        description="このチャンネルの吊り下げを編集します",
    )
    @app_commands.describe(
        image="新しく差し替える画像（任意）",
        remove_image="現在の画像を削除する",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(
        administrator=True
    )
    @app_commands.checks.has_permissions(
        administrator=True
    )
    async def tsuri_edit(
        self,
        interaction: discord.Interaction,
        image: Optional[discord.Attachment] = None,
        remove_image: bool = False,
    ):
        if not await self.validate_command(
            interaction
        ):
            return

        current_setting = self.get_setting(
            interaction.channel.id
        )

        if current_setting is None:
            await interaction.response.send_message(
                "⚠️ 吊り下げが設定されていません。\n"
                "`/tsuri_set`を使用してください。",
                ephemeral=True,
            )
            return

        if image and remove_image:
            await interaction.response.send_message(
                "❌ 画像の差し替えと削除は"
                "同時に指定できません。",
                ephemeral=True,
            )
            return

        if image:
            error_message = self.validate_image(
                image
            )

            if error_message:
                await interaction.response.send_message(
                    f"❌ {error_message}",
                    ephemeral=True,
                )
                return

        await interaction.response.send_modal(
            TsuriModal(
                cog=self,
                channel=interaction.channel,
                current_text=current_setting.get(
                    "text",
                    "",
                ),
                image_attachment=image,
                remove_image=remove_image,
                is_edit=True,
            )
        )

    @app_commands.command(
        name="tsuri_off",
        description="このチャンネルの吊り下げを解除します",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(
        administrator=True
    )
    @app_commands.checks.has_permissions(
        administrator=True
    )
    async def tsuri_off(
        self,
        interaction: discord.Interaction,
    ):
        if not await self.validate_command(
            interaction
        ):
            return

        await interaction.response.defer(
            ephemeral=True
        )

        channel = interaction.channel
        current_setting = self.get_setting(
            channel.id
        )

        if current_setting is None:
            await interaction.followup.send(
                "⚠️ 吊り下げは設定されていません。",
                ephemeral=True,
            )
            return

        task = self.refresh_tasks.pop(
            channel.id,
            None,
        )

        if task and not task.done():
            task.cancel()

        lock = self.get_channel_lock(
            channel.id
        )

        async with lock:
            await self._update_setting(
                channel_id=channel.id,
                setting=None,
            )

            await self.delete_messages(
                channel=channel,
                message_ids=self.get_message_ids(
                    current_setting
                ),
            )

            self.delete_local_image(
                current_setting.get(
                    "image_path"
                )
            )

        await interaction.followup.send(
            "✅ 吊り下げを解除しました。",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(
        TsuriManager(bot)
    )