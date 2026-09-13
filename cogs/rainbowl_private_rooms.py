# cogs/rainbowl_private_rooms.py
"""
rainbowl専用：プライベートルーム機能（プライベート会議）。

Discordイベント・インタラクションの受け口はこのファイルに、
判定・DynamoDB操作・チャンネル操作は
services/private_room_service.py に集約する。

対応する部屋種別は「プライベート会議」「プライベート個室」の2種別
（プライベートルーム機能.md 1章＋個室は会議の1対1版）。
"""

import asyncio
import traceback

import discord
from discord import app_commands
from discord.ext import commands, tasks

from services import private_room_service
from services.private_room_service import PrivateRoomError
from utils.interaction_cooldown import is_on_cooldown


CREATE_BUTTON_PREFIX = "private_room_create:"
DELETE_BUTTON_CUSTOM_ID = "private_room_delete_own"

GENERIC_ERROR_MESSAGE = (
    "予期しないエラーが発生しました。運営へお問い合わせください。"
)


async def _send_generic_error(interaction: discord.Interaction) -> None:
    """
    想定外の例外発生時、defer済みインタラクションを「考え中」のまま
    放置しない（Discordのwebhookトークンが切れるまで固まり続けるため）。
    """
    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                GENERIC_ERROR_MESSAGE, ephemeral=True
            )
        else:
            await interaction.response.send_message(
                GENERIC_ERROR_MESSAGE, ephemeral=True
            )
    except discord.HTTPException:
        pass


class _BaseView(discord.ui.View):
    """未捕捉の例外でインタラクションを「考え中」のまま固まらせない基底View。"""

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item,
    ) -> None:
        print(f"[private_room] View error (item={item}): {error!r}")
        traceback.print_exception(type(error), error, error.__traceback__)
        await _send_generic_error(interaction)


class _BaseModal(discord.ui.Modal):
    """未捕捉の例外でインタラクションを「考え中」のまま固まらせない基底Modal。"""

    async def on_error(
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        print(f"[private_room] Modal error: {error!r}")
        traceback.print_exception(type(error), error, error.__traceback__)
        await _send_generic_error(interaction)


GENERAL_COOLDOWN_KEY = "vc_room_menu_op"
INVITE_COOLDOWN_KEY = "vc_room_invite_op"


def _check_cooldown_message(
    interaction: discord.Interaction, key: str, seconds: float
) -> bool:
    """クールダウン中ならTrueを返す（呼び出し側で早期returnする）。"""
    return is_on_cooldown((interaction.user.id, key), seconds)


# =========================================================
#   作成モーダル → 作成（ビットレートはデフォルト固定）
# =========================================================
class RoomCreateModal(_BaseModal, title="プライベートルーム作成"):
    human_limit = discord.ui.TextInput(
        label="人数（空欄で無制限・3〜99人）",
        required=False,
        max_length=3,
    )
    room_name = discord.ui.TextInput(
        label="部屋名（空欄で「(表示名)'s ROOM」）",
        required=False,
        max_length=80,
    )

    def __init__(self, category_id: int):
        super().__init__()
        self.category_id = category_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            limit = private_room_service.parse_human_limit(
                self.human_limit.value
            )
        except PrivateRoomError as exc:
            await interaction.response.send_message(
                str(exc), ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            channel, corrected, actual_bitrate = (
                await private_room_service.create_room(
                    interaction.guild,
                    interaction.user,
                    self.category_id,
                    self.room_name.value,
                    limit,
                    private_room_service.DEFAULT_BITRATE_BPS,
                )
            )
        except PrivateRoomError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        message = f"ルームを作成しました → {channel.mention}"
        if corrected:
            message += (
                f"\nデフォルトのビットレートは現在のサーバーでは"
                f"設定できません。設定可能な最大値である"
                f"{actual_bitrate}bpsに変更しました。"
            )
        await interaction.followup.send(message, ephemeral=True)


# =========================================================
#   作成モーダル（個室） → 相手選択 → 作成
# =========================================================
class SoloRoomCreateModal(_BaseModal, title="プライベート個室作成"):
    room_name = discord.ui.TextInput(
        label="部屋名（空欄で「(表示名)'s ROOM」）",
        required=False,
        max_length=80,
    )

    def __init__(self, category_id: int):
        super().__init__()
        self.category_id = category_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        view = SoloPartnerSelectView(
            self.category_id, self.room_name.value
        )
        await interaction.response.send_message(
            "一緒に入る相手を選択してください。",
            view=view,
            ephemeral=True,
        )


class SoloPartnerSelect(discord.ui.UserSelect):
    def __init__(self, category_id: int, room_name_raw: str):
        super().__init__(
            placeholder="相手を選択してください",
            min_values=1,
            max_values=1,
        )
        self.category_id = category_id
        self.room_name_raw = room_name_raw

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0]
        partner = interaction.guild.get_member(selected.id)

        if partner is None:
            await interaction.response.send_message(
                "選択したユーザーが見つかりませんでした。"
                "サーバーに参加しているユーザーを選んでください。",
                ephemeral=True,
            )
            return
        if partner.bot:
            await interaction.response.send_message(
                "Botユーザーは選択できません。", ephemeral=True
            )
            return
        if partner.id == interaction.user.id:
            await interaction.response.send_message(
                "自分自身は選択できません。", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            channel, corrected, actual_bitrate = (
                await private_room_service.create_room(
                    interaction.guild,
                    interaction.user,
                    self.category_id,
                    self.room_name_raw,
                    private_room_service.SOLO_ROOM_HUMAN_LIMIT,
                    private_room_service.DEFAULT_BITRATE_BPS,
                    room_type=private_room_service.ROOM_TYPE_PRIVATE_SOLO,
                    initial_invited_ids=[partner.id],
                )
            )
        except PrivateRoomError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        message = f"ルームを作成しました → {channel.mention}"
        if corrected:
            message += (
                f"\nデフォルトのビットレートは現在のサーバーでは"
                f"設定できません。設定可能な最大値である"
                f"{actual_bitrate}bpsに変更しました。"
            )
        await interaction.followup.send(message, ephemeral=True)


class SoloPartnerSelectView(_BaseView):
    def __init__(self, category_id: int, room_name_raw: str):
        super().__init__(timeout=120)
        self.add_item(SoloPartnerSelect(category_id, room_name_raw))


# =========================================================
#   作成メニュー・削除ボタン（永続View）
# =========================================================
CREATE_BUTTON_LABELS = {
    private_room_service.ROOM_TYPE_PRIVATE_MEETING: "🔒 プライベート会議を作成",
    private_room_service.ROOM_TYPE_PRIVATE_SOLO: "🔒 プライベート個室を作成",
}


class CreateRoomButton(discord.ui.Button):
    def __init__(self, room_type: str, category_id: int):
        super().__init__(
            label=CREATE_BUTTON_LABELS.get(
                room_type, "🔒 プライベートルームを作成"
            ),
            style=discord.ButtonStyle.primary,
            custom_id=f"{CREATE_BUTTON_PREFIX}{room_type}:{category_id}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.bot:
            await interaction.response.send_message(
                "Botユーザーはこの機能を利用できません。",
                ephemeral=True,
            )
            return

        room_type, category_id_text = self.custom_id[
            len(CREATE_BUTTON_PREFIX):
        ].split(":", 1)
        category_id = int(category_id_text)

        if room_type == private_room_service.ROOM_TYPE_PRIVATE_SOLO:
            await interaction.response.send_modal(
                SoloRoomCreateModal(category_id)
            )
        else:
            await interaction.response.send_modal(
                RoomCreateModal(category_id)
            )


class CreateRoomButtonView(_BaseView):
    def __init__(self, room_type: str, category_id: int):
        super().__init__(timeout=None)
        self.add_item(CreateRoomButton(room_type, category_id))


class DeleteOwnRoomButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="🗑️ 自分のルームを削除",
            style=discord.ButtonStyle.danger,
            custom_id=DELETE_BUTTON_CUSTOM_ID,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await private_room_service.request_manual_delete(
                interaction.guild, interaction.user
            )
        except PrivateRoomError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        await interaction.followup.send(
            "ルームを削除しました。", ephemeral=True
        )


class DeleteOwnRoomButtonView(_BaseView):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(DeleteOwnRoomButton())


# =========================================================
#   ルームメニュー（VCインチャに投稿する永続View）
# =========================================================
class RenameModal(_BaseModal, title="部屋名変更"):
    new_name = discord.ui.TextInput(label="新しい部屋名", max_length=80)

    def __init__(self, channel_id: int):
        super().__init__()
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        channel = interaction.guild.get_channel(self.channel_id)
        try:
            new_name = await private_room_service.rename_room(
                interaction.guild,
                interaction.user,
                channel,
                self.new_name.value,
            )
        except PrivateRoomError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        await interaction.followup.send(
            f"部屋名を「{new_name}」に変更しました。", ephemeral=True
        )


class StatusModal(_BaseModal, title="ステータス変更"):
    status_text = discord.ui.TextInput(
        label="ステータス（空欄で解除）",
        required=False,
        max_length=80,
    )

    def __init__(self, channel_id: int):
        super().__init__()
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        channel = interaction.guild.get_channel(self.channel_id)
        try:
            result = await private_room_service.set_status(
                interaction.guild,
                interaction.user,
                channel,
                self.status_text.value,
            )
        except PrivateRoomError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        message = (
            "ステータスを解除しました。"
            if result is None
            else f"ステータスを「{result}」に変更しました。"
        )
        await interaction.followup.send(message, ephemeral=True)


class LimitModal(_BaseModal, title="人数変更"):
    new_limit = discord.ui.TextInput(
        label="人数（空欄で無制限・3〜99人）",
        required=False,
        max_length=3,
    )

    def __init__(self, channel_id: int):
        super().__init__()
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        channel = interaction.guild.get_channel(self.channel_id)
        try:
            new_limit = await private_room_service.change_limit(
                interaction.guild,
                interaction.user,
                channel,
                self.new_limit.value,
            )
        except PrivateRoomError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        label = "無制限" if new_limit is None else f"{new_limit}人"
        await interaction.followup.send(
            f"人数上限を{label}に変更しました。", ephemeral=True
        )


class ChangeBitrateSelect(discord.ui.Select):
    def __init__(self, channel_id: int):
        options = [
            discord.SelectOption(label=label, value=str(bps))
            for label, bps in private_room_service.BITRATE_CHOICES
        ]
        super().__init__(
            placeholder="変更後のビットレートを選択してください",
            options=options,
        )
        self.channel_id = channel_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        channel = interaction.guild.get_channel(self.channel_id)
        try:
            actual_bitrate, corrected = (
                await private_room_service.change_bitrate(
                    interaction.guild,
                    interaction.user,
                    channel,
                    int(self.values[0]),
                )
            )
        except PrivateRoomError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        message = f"ビットレートを{actual_bitrate}bpsに変更しました。"
        if corrected:
            message += (
                "\n選択された値は現在のサーバーでは設定できないため、"
                "設定可能な最大値に自動補正しました。"
            )
        await interaction.followup.send(message, ephemeral=True)


class ChangeBitrateSelectView(_BaseView):
    def __init__(self, channel_id: int):
        super().__init__(timeout=120)
        self.add_item(ChangeBitrateSelect(channel_id))


class InviteUserSelect(discord.ui.UserSelect):
    def __init__(self, channel_id: int):
        super().__init__(
            placeholder="招待するユーザーを選択してください",
            min_values=1,
            max_values=25,
        )
        self.channel_id = channel_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        channel = interaction.guild.get_channel(self.channel_id)
        try:
            invited = await private_room_service.invite_users(
                interaction.guild,
                interaction.user,
                channel,
                self.values,
            )
        except PrivateRoomError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        names = "、".join(u.display_name for u in invited)
        await interaction.followup.send(
            f"{names} を招待しました。", ephemeral=True
        )


class InviteUserSelectView(_BaseView):
    def __init__(self, channel_id: int):
        super().__init__(timeout=120)
        self.add_item(InviteUserSelect(channel_id))


class UninviteUserSelect(discord.ui.UserSelect):
    def __init__(self, channel_id: int):
        super().__init__(
            placeholder="招待解除するユーザーを選択してください",
            min_values=1,
            max_values=25,
        )
        self.channel_id = channel_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        channel = interaction.guild.get_channel(self.channel_id)

        done = []
        errors = []
        for user in self.values:
            try:
                await private_room_service.uninvite_user(
                    interaction.guild,
                    interaction.user,
                    channel,
                    user.id,
                )
                done.append(user.display_name)
            except PrivateRoomError as exc:
                errors.append(f"{user.display_name}: {exc}")

        parts = []
        if done:
            parts.append("解除しました: " + "、".join(done))
        if errors:
            parts.append("失敗:\n" + "\n".join(errors))
        await interaction.followup.send(
            "\n".join(parts) or "変更はありませんでした。",
            ephemeral=True,
        )


class UninviteUserSelectView(_BaseView):
    def __init__(self, channel_id: int):
        super().__init__(timeout=120)
        self.add_item(UninviteUserSelect(channel_id))


class InvitedListView(_BaseView):
    def __init__(
        self,
        channel_id: int,
        guild_id: int,
        page: int,
        max_page: int,
    ):
        super().__init__(timeout=120)
        self.channel_id = channel_id
        self.guild_id = guild_id
        self.page = page
        self.max_page = max_page
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        self.prev_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self.max_page - 1

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.page -= 1
        await self._refresh(interaction)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.page += 1
        await self._refresh(interaction)

    async def _refresh(self, interaction: discord.Interaction) -> None:
        # ページ送りのたびにDBへ問い合わせるため、先にdeferして
        # Discordの3秒応答期限を確実に回避してから更新する。
        await interaction.response.defer()

        try:
            room = await private_room_service.get_room_for_channel(
                self.guild_id, self.channel_id
            )
        except PrivateRoomError as exc:
            await interaction.edit_original_response(
                content=str(exc), embed=None, view=None
            )
            return

        embed, self.max_page = (
            private_room_service.build_invited_list_embed(
                room, interaction.guild, self.page, 10
            )
        )
        self._sync_buttons()
        await interaction.edit_original_response(embed=embed, view=self)


class RoomMenuView(_BaseView):
    """
    プライベート会議のVCインチャに投稿する永続View。

    どのメッセージ／どのチャンネルでも interaction.channel から
    対象VCを特定できるため、custom_idに部屋固有の情報は含めない。
    Bot再起動後は setup() で1度add_viewするだけでよい。
    """

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="部屋名変更",
        style=discord.ButtonStyle.secondary,
        custom_id="private_room_menu:rename",
        row=0,
    )
    async def rename_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if _check_cooldown_message(
            interaction,
            GENERAL_COOLDOWN_KEY,
            private_room_service.ROOM_MENU_COOLDOWN_SECONDS,
        ):
            await interaction.response.send_message(
                "少し間隔をあけてから操作してください。", ephemeral=True
            )
            return
        if not isinstance(interaction.channel, discord.VoiceChannel):
            await interaction.response.send_message(
                "VCインチャ内で使用してください。", ephemeral=True
            )
            return
        await interaction.response.send_modal(
            RenameModal(interaction.channel.id)
        )

    @discord.ui.button(
        label="ステータス変更",
        style=discord.ButtonStyle.secondary,
        custom_id="private_room_menu:status",
        row=0,
    )
    async def status_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if _check_cooldown_message(
            interaction,
            GENERAL_COOLDOWN_KEY,
            private_room_service.ROOM_MENU_COOLDOWN_SECONDS,
        ):
            await interaction.response.send_message(
                "少し間隔をあけてから操作してください。", ephemeral=True
            )
            return
        if not isinstance(interaction.channel, discord.VoiceChannel):
            await interaction.response.send_message(
                "VCインチャ内で使用してください。", ephemeral=True
            )
            return
        await interaction.response.send_modal(
            StatusModal(interaction.channel.id)
        )

    @discord.ui.button(
        label="人数変更",
        style=discord.ButtonStyle.secondary,
        custom_id="private_room_menu:limit",
        row=0,
    )
    async def limit_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if _check_cooldown_message(
            interaction,
            GENERAL_COOLDOWN_KEY,
            private_room_service.ROOM_MENU_COOLDOWN_SECONDS,
        ):
            await interaction.response.send_message(
                "少し間隔をあけてから操作してください。", ephemeral=True
            )
            return
        if not isinstance(interaction.channel, discord.VoiceChannel):
            await interaction.response.send_message(
                "VCインチャ内で使用してください。", ephemeral=True
            )
            return
        await interaction.response.send_modal(
            LimitModal(interaction.channel.id)
        )

    @discord.ui.button(
        label="ビットレート変更",
        style=discord.ButtonStyle.secondary,
        custom_id="private_room_menu:bitrate",
        row=0,
    )
    async def bitrate_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if _check_cooldown_message(
            interaction,
            GENERAL_COOLDOWN_KEY,
            private_room_service.ROOM_MENU_COOLDOWN_SECONDS,
        ):
            await interaction.response.send_message(
                "少し間隔をあけてから操作してください。", ephemeral=True
            )
            return
        if not isinstance(interaction.channel, discord.VoiceChannel):
            await interaction.response.send_message(
                "VCインチャ内で使用してください。", ephemeral=True
            )
            return
        view = ChangeBitrateSelectView(interaction.channel.id)
        await interaction.response.send_message(
            "変更後のビットレートを選択してください。",
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(
        label="ユーザー招待",
        style=discord.ButtonStyle.success,
        custom_id="private_room_menu:invite",
        row=1,
    )
    async def invite_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if _check_cooldown_message(
            interaction,
            INVITE_COOLDOWN_KEY,
            private_room_service.INVITE_COOLDOWN_SECONDS,
        ):
            await interaction.response.send_message(
                "少し間隔をあけてから操作してください。", ephemeral=True
            )
            return
        if not isinstance(interaction.channel, discord.VoiceChannel):
            await interaction.response.send_message(
                "VCインチャ内で使用してください。", ephemeral=True
            )
            return
        view = InviteUserSelectView(interaction.channel.id)
        await interaction.response.send_message(
            "招待するユーザーを選択してください。",
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(
        label="招待解除",
        style=discord.ButtonStyle.danger,
        custom_id="private_room_menu:uninvite",
        row=1,
    )
    async def uninvite_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if _check_cooldown_message(
            interaction,
            INVITE_COOLDOWN_KEY,
            private_room_service.INVITE_COOLDOWN_SECONDS,
        ):
            await interaction.response.send_message(
                "少し間隔をあけてから操作してください。", ephemeral=True
            )
            return
        if not isinstance(interaction.channel, discord.VoiceChannel):
            await interaction.response.send_message(
                "VCインチャ内で使用してください。", ephemeral=True
            )
            return
        view = UninviteUserSelectView(interaction.channel.id)
        await interaction.response.send_message(
            "招待解除するユーザーを選択してください。",
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(
        label="招待済みユーザー確認",
        style=discord.ButtonStyle.secondary,
        custom_id="private_room_menu:list",
        row=1,
    )
    async def list_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not isinstance(interaction.channel, discord.VoiceChannel):
            await interaction.response.send_message(
                "VCインチャ内で使用してください。", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            room = await private_room_service.get_room_for_channel(
                interaction.guild.id, interaction.channel.id
            )
        except PrivateRoomError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        if not private_room_service.is_operator(
            interaction.user, room
        ):
            await interaction.followup.send(
                "このルームの作成者・招待ユーザー・管理者のみ確認できます。",
                ephemeral=True,
            )
            return

        embed, max_page = private_room_service.build_invited_list_embed(
            room, interaction.guild, 0, 10
        )
        view = (
            InvitedListView(
                interaction.channel.id, interaction.guild.id, 0, max_page
            )
            if max_page > 1
            else None
        )
        await interaction.followup.send(
            embed=embed, view=view, ephemeral=True
        )


# =========================================================
#   Cog本体
# =========================================================
class RainbowlPrivateRooms(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._startup_done = False
        self.room_maintenance_loop.start()

    def cog_unload(self) -> None:
        self.room_maintenance_loop.cancel()

    async def _is_rainbowl_guild(self, guild_id: int) -> bool:
        return await asyncio.to_thread(
            private_room_service.is_rainbowl_guild, guild_id
        )

    async def _require_rainbowl(
        self, interaction: discord.Interaction
    ) -> bool:
        """
        呼び出し側は必ず先に interaction.response.defer() していること。

        DynamoDBへの問い合わせ（is_rainbowl_guild）は、Botのコールド
        スタート直後などboto3の認証情報解決に時間がかかることがあり、
        deferより前に呼ぶとDiscordの3秒応答期限に間に合わず
        「アプリケーションが応答しませんでした」になる。
        """
        if not await self._is_rainbowl_guild(interaction.guild_id):
            await interaction.followup.send(
                "このサーバーではこの機能は有効になっていません。",
                ephemeral=True,
            )
            return False
        return True

    # ========================================
    # 起動時：永続Viewの再登録
    # ========================================
    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._startup_done:
            return
        self._startup_done = True

        self.bot.add_view(RoomMenuView())
        self.bot.add_view(DeleteOwnRoomButtonView())

        for guild in self.bot.guilds:
            if not await self._is_rainbowl_guild(guild.id):
                continue

            try:
                menus = await private_room_service.get_menus(guild.id)
            except Exception as exc:
                print(
                    "[private_room] メニュー読み込み失敗"
                    f" guild_id={guild.id} error={exc}"
                )
                continue

            for menu in menus:
                try:
                    self.bot.add_view(
                        CreateRoomButtonView(
                            menu["room_type"],
                            int(menu["category_id"]),
                        )
                    )
                except Exception as exc:
                    print(
                        "[private_room] 作成ボタンView再登録失敗"
                        f" guild_id={guild.id} error={exc}"
                    )

    # ========================================
    # ボイス状態変化：無人タイマー・解除待ちの確定
    # ========================================
    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if before.channel == after.channel:
            return

        if before.channel is not None and isinstance(
            before.channel, discord.VoiceChannel
        ):
            if await self._is_rainbowl_guild(before.channel.guild.id):
                await self._handle_leave(
                    before.channel.guild, before.channel, member
                )
                if member.bot:
                    await private_room_service.adjust_limit_for_bot_presence(
                        before.channel.guild, before.channel
                    )

        if after.channel is not None and isinstance(
            after.channel, discord.VoiceChannel
        ):
            if await self._is_rainbowl_guild(after.channel.guild.id):
                await self._handle_join(
                    after.channel.guild, after.channel
                )
                if member.bot:
                    await private_room_service.adjust_limit_for_bot_presence(
                        after.channel.guild, after.channel
                    )

    async def _handle_leave(
        self,
        guild: discord.Guild,
        channel: discord.VoiceChannel,
        member: discord.Member,
    ) -> None:
        try:
            await private_room_service.finalize_pending_removal(
                guild, channel, member.id
            )
        except Exception as exc:
            print(f"[private_room] finalize_pending_removal error: {exc}")

        if private_room_service.human_member_count(channel) == 0:
            try:
                await private_room_service.on_channel_became_empty(
                    guild, channel
                )
            except Exception as exc:
                print(
                    f"[private_room] on_channel_became_empty error: {exc}"
                )

    async def _handle_join(
        self, guild: discord.Guild, channel: discord.VoiceChannel
    ) -> None:
        try:
            await private_room_service.on_channel_became_occupied(
                guild, channel
            )
        except Exception as exc:
            print(
                f"[private_room] on_channel_became_occupied error: {exc}"
            )

    # ========================================
    # カテゴリ移動防止・権限自動修復（差分検知）
    # ========================================
    @commands.Cog.listener()
    async def on_guild_channel_update(
        self,
        before: discord.abc.GuildChannel,
        after: discord.abc.GuildChannel,
    ) -> None:
        if not isinstance(after, discord.VoiceChannel):
            return
        if not await self._is_rainbowl_guild(after.guild.id):
            return

        try:
            await private_room_service.restore_category(
                after.guild, after
            )
            await private_room_service.repair_room_permissions(
                after.guild, after
            )
        except Exception as exc:
            print(
                "[private_room] on_guild_channel_update repair error:"
                f" {exc}"
            )

    # ========================================
    # Discord側からの手動VC削除の検知
    # ========================================
    @commands.Cog.listener()
    async def on_guild_channel_delete(
        self, channel: discord.abc.GuildChannel
    ) -> None:
        if not isinstance(channel, discord.VoiceChannel):
            return
        guild = channel.guild
        if not await self._is_rainbowl_guild(guild.id):
            return

        room = await asyncio.to_thread(
            private_room_service.store.get_room, guild.id, channel.id
        )
        if not room:
            return

        await asyncio.to_thread(
            private_room_service.store.delete_room,
            guild.id,
            int(room["owner_id"]),
            channel.id,
        )
        await private_room_service.log_event(
            guild,
            "🗑️ VC外部削除の検知",
            "Discord側からVCが手動削除されたため、"
            "アクティブルームDBから除去しました。",
            チャンネル名=room.get("room_name", "?"),
        )

    # ========================================
    # サーバー退出：招待ユーザーの後片付け（23章）
    # ========================================
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        guild = member.guild
        if not await self._is_rainbowl_guild(guild.id):
            return

        rooms = await asyncio.to_thread(
            private_room_service.store.list_active_rooms, guild.id
        )
        now = private_room_service.now_iso()

        for room in rooms:
            invited = room.get("invited_user_ids") or []
            if str(member.id) not in invited:
                continue

            channel_id = int(room["channel_id"])
            await asyncio.to_thread(
                private_room_service.store.remove_invited_user,
                guild.id,
                channel_id,
                member.id,
                now,
            )

            channel = guild.get_channel(channel_id)
            if isinstance(channel, discord.VoiceChannel):
                try:
                    await private_room_service.repair_room_permissions(
                        guild, channel
                    )
                except Exception as exc:
                    print(
                        "[private_room] on_member_remove repair error:"
                        f" {exc}"
                    )

    # ========================================
    # 定期処理：無人自動削除・未送信ログ再送・権限整合性確認
    # ========================================
    @tasks.loop(seconds=60)
    async def room_maintenance_loop(self) -> None:
        for guild in list(self.bot.guilds):
            if not await self._is_rainbowl_guild(guild.id):
                continue

            try:
                await private_room_service.auto_delete_empty_rooms(
                    guild
                )
            except Exception as exc:
                print(
                    "[private_room] auto_delete_empty_rooms error"
                    f" (guild={guild.id}): {exc}"
                )

            try:
                await private_room_service.flush_log_queue(guild)
            except Exception as exc:
                print(
                    "[private_room] flush_log_queue error"
                    f" (guild={guild.id}): {exc}"
                )

            try:
                await self._periodic_permission_repair(guild)
            except Exception as exc:
                print(
                    "[private_room] periodic_permission_repair error"
                    f" (guild={guild.id}): {exc}"
                )

    async def _periodic_permission_repair(
        self, guild: discord.Guild
    ) -> None:
        rooms = await asyncio.to_thread(
            private_room_service.store.list_active_rooms, guild.id
        )
        for room in rooms:
            channel = guild.get_channel(int(room["channel_id"]))
            if isinstance(channel, discord.VoiceChannel):
                await private_room_service.repair_room_permissions(
                    guild, channel
                )

    @room_maintenance_loop.before_loop
    async def before_room_maintenance_loop(self) -> None:
        await self.bot.wait_until_ready()

        for guild in list(self.bot.guilds):
            if not await self._is_rainbowl_guild(guild.id):
                continue
            try:
                await private_room_service.reconcile_guild(
                    self.bot, guild
                )
            except Exception as exc:
                print(
                    "[private_room] reconcile_guild error"
                    f" (guild={guild.id}): {exc}"
                )

    # ========================================
    # 管理者コマンド
    # ========================================
    @app_commands.command(
        name="set_vc_log_channel",
        description=(
            "プライベートルーム機能の操作ログ投稿先を設定します"
            "（管理者専用）"
        ),
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(channel="ログ投稿先のテキストチャンネル")
    async def set_vc_log_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not await self._require_rainbowl(interaction):
            return

        await private_room_service.set_log_channel(
            interaction.guild_id, channel.id
        )
        await interaction.followup.send(
            f"操作ログの投稿先を {channel.mention} に設定しました。",
            ephemeral=True,
        )

    @app_commands.command(
        name="create_vc_menu",
        description="プライベートルーム作成メニューを設置します（管理者専用）",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        room_type="部屋の種別", category="作成先カテゴリ"
    )
    @app_commands.choices(
        room_type=[
            app_commands.Choice(name=label, value=value)
            for label, value in private_room_service.ROOM_TYPE_CHOICES
        ]
    )
    async def create_vc_menu(
        self,
        interaction: discord.Interaction,
        room_type: app_commands.Choice[str],
        category: discord.CategoryChannel,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not await self._require_rainbowl(interaction):
            return

        existing_menus = await private_room_service.get_menus(
            interaction.guild_id
        )
        if any(
            menu.get("room_type") == room_type.value
            and str(menu.get("category_id")) == str(category.id)
            for menu in existing_menus
        ):
            await interaction.followup.send(
                f"このカテゴリには既に{room_type.name}の"
                "作成メニューが設置されています。",
                ephemeral=True,
            )
            return

        if not private_room_service.category_is_private_safe(category):
            await interaction.followup.send(
                "このカテゴリは@everyoneまたは他ロールに"
                "「チャンネルを見る」が許可されており、"
                "プライベート性を保証できないため設置できません。",
                ephemeral=True,
            )
            return

        message = await interaction.channel.send(
            view=CreateRoomButtonView(room_type.value, category.id),
        )

        added = await private_room_service.add_menu(
            interaction.guild_id,
            room_type.value,
            category.id,
            interaction.channel_id,
            message.id,
        )
        if not added:
            await message.delete()
            await interaction.followup.send(
                "設置に失敗しました（重複）。もう一度お試しください。",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"作成メニューを設置しました → {message.jump_url}",
            ephemeral=True,
        )

    @app_commands.command(
        name="list_vc_menus",
        description=(
            "設置済みのプライベートルーム作成メニュー一覧を表示します"
            "（管理者専用）"
        ),
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def list_vc_menus(
        self, interaction: discord.Interaction
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not await self._require_rainbowl(interaction):
            return

        menus = await private_room_service.get_menus(
            interaction.guild_id
        )
        if not menus:
            await interaction.followup.send(
                "作成メニューはまだ設置されていません。",
                ephemeral=True,
            )
            return

        lines = []
        for menu in menus:
            category = interaction.guild.get_channel(
                int(menu["category_id"])
            )
            channel = interaction.guild.get_channel(
                int(menu["menu_channel_id"])
            )
            missing_note = (
                " ⚠️CATEGORY_MISSING"
                if menu.get("category_missing")
                else ""
            )
            room_type_label = private_room_service.ROOM_TYPE_DISPLAY_NAMES.get(
                menu.get("room_type"), menu.get("room_type")
            )
            lines.append(
                f"・{room_type_label} / "
                f"{category.mention if category else menu['category_id']}"
                f" / {channel.mention if channel else '?'}"
                f"{missing_note}"
            )

        await interaction.followup.send(
            "\n".join(lines), ephemeral=True
        )

    @app_commands.command(
        name="delete_vc_menu",
        description=(
            "プライベートルーム作成メニューを削除します"
            "（既存ルームは削除しません・管理者専用）"
        ),
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        room_type="部屋の種別", category="対象の作成先カテゴリ"
    )
    @app_commands.choices(
        room_type=[
            app_commands.Choice(name=label, value=value)
            for label, value in private_room_service.ROOM_TYPE_CHOICES
        ]
    )
    async def delete_vc_menu(
        self,
        interaction: discord.Interaction,
        room_type: app_commands.Choice[str],
        category: discord.CategoryChannel,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not await self._require_rainbowl(interaction):
            return

        menus = await private_room_service.get_menus(
            interaction.guild_id
        )
        target = next(
            (
                menu
                for menu in menus
                if menu.get("room_type") == room_type.value
                and str(menu.get("category_id")) == str(category.id)
            ),
            None,
        )
        if target is None:
            await interaction.followup.send(
                "該当する作成メニューが見つかりませんでした。",
                ephemeral=True,
            )
            return

        await private_room_service.remove_menu(
            interaction.guild_id, room_type.value, category.id
        )

        channel = interaction.guild.get_channel(
            int(target["menu_channel_id"])
        )
        if isinstance(channel, discord.TextChannel):
            try:
                message = await channel.fetch_message(
                    int(target["message_id"])
                )
                await message.delete()
            except discord.HTTPException:
                pass

        await interaction.followup.send(
            "作成メニューを削除しました（既存のルームは削除されません）。",
            ephemeral=True,
        )

    @app_commands.command(
        name="restore_vc_room_menu",
        description="VCインチャのルームメニューを再投稿します（管理者専用）",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(channel="対象のプライベート会議VC")
    async def restore_vc_room_menu(
        self,
        interaction: discord.Interaction,
        channel: discord.VoiceChannel,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not await self._require_rainbowl(interaction):
            return

        room = await asyncio.to_thread(
            private_room_service.store.get_room,
            interaction.guild_id,
            channel.id,
        )
        if room is None:
            await interaction.followup.send(
                "このVCはZeroBot管理下のプライベートルームではありません。",
                ephemeral=True,
            )
            return

        owner = interaction.guild.get_member(int(room["owner_id"]))
        embed = (
            private_room_service.build_room_menu_embed(owner)
            if owner
            else discord.Embed(title="🔒 プライベートルーム メニュー")
        )

        message = await channel.send(
            content=(
                f"{owner.mention if owner else room['owner_id']} "
                "さんのプライベートルームです。"
            ),
            embed=embed,
            view=RoomMenuView(),
        )

        await asyncio.to_thread(
            private_room_service.store.update_room_menu_message_id,
            interaction.guild_id,
            channel.id,
            message.id,
            private_room_service.now_iso(),
        )

        await interaction.followup.send(
            f"ルームメニューを再投稿しました → {message.jump_url}",
            ephemeral=True,
        )

    @app_commands.command(
        name="vc_system_on",
        description="プライベートルーム機能を再開します（管理者専用）",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def vc_system_on(
        self, interaction: discord.Interaction
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not await self._require_rainbowl(interaction):
            return
        await private_room_service.set_system_enabled(
            interaction.guild_id, True
        )
        await interaction.followup.send(
            "プライベートルーム機能を再開しました。", ephemeral=True
        )

    @app_commands.command(
        name="vc_system_off",
        description="プライベートルーム機能を緊急停止します（管理者専用）",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def vc_system_off(
        self, interaction: discord.Interaction
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not await self._require_rainbowl(interaction):
            return
        await private_room_service.set_system_enabled(
            interaction.guild_id, False
        )
        await interaction.followup.send(
            "プライベートルーム機能を停止しました。", ephemeral=True
        )

    @app_commands.command(
        name="vc_system_status",
        description="プライベートルーム機能の稼働状態を確認します（管理者専用）",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def vc_system_status(
        self, interaction: discord.Interaction
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not await self._require_rainbowl(interaction):
            return
        enabled = await asyncio.to_thread(
            private_room_service.is_system_enabled,
            interaction.guild_id,
        )
        await interaction.followup.send(
            f"現在の状態: {'🟢 稼働中' if enabled else '🔴 停止中'}",
            ephemeral=True,
        )

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """
        管理者コマンド内の未捕捉例外で「考え中」のまま固まらせない。
        defer済みの場合はfollowup、未応答ならresponseでエラーを返す。
        """
        original = getattr(error, "original", error)
        print(
            f"[private_room] スラッシュコマンドエラー"
            f" command={interaction.command}: {original!r}"
        )
        traceback.print_exception(
            type(original), original, original.__traceback__
        )
        await _send_generic_error(interaction)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RainbowlPrivateRooms(bot))
