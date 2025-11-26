import asyncio
import discord
from utils.messages import get_random_success_message
from data.guild_config_store import GuildConfigStore

countdown_lock = asyncio.Lock()
countdown_active = {}

# ★ 設定読み出し用
config_store = GuildConfigStore()


async def set_countdown_active(user_id, value):
    async with countdown_lock:
        countdown_active[user_id] = value

class StopButtonView(discord.ui.View):
    def __init__(self, guild_id, target_member_id, command_user_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.target_member_id = target_member_id
        self.command_user_id = command_user_id

    @discord.ui.button(label="STOP", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):

        # ★ ギルド設定から STOP ボタン制御を取得
        cfg = config_store.get_config(self.guild_id) or {}
        oyanmo_cfg = cfg.get("oyanmo") or {}

        stop_button_only_owner = oyanmo_cfg.get("enable_stop_button", True)
        only_cmd_user = oyanmo_cfg.get("stop_button_only_command_user", True)

        # STOP ボタン自体が disabled の鯖
        if not stop_button_only_owner:
            await interaction.response.send_message("❌ STOPボタンはこのサーバーでは無効です。", ephemeral=True)
            return

        # コマンド実行者だけに限定するか？
        if only_cmd_user and interaction.user.id != self.command_user_id:
            await interaction.response.send_message("❌ 停止権限がありません。", ephemeral=True)
            return

        # 停止
        countdown_active[self.target_member_id] = False
        await interaction.response.defer()

async def countdown_procedure(interaction, target_member, target_channel, countdown_msg):
    guild = interaction.guild
    guild_id = guild.id

    # ★ ギルド設定読み込み
    cfg = config_store.get_config(guild_id) or {}
    oyanmo_cfg = cfg.get("oyanmo") or {}

    countdown_seconds = oyanmo_cfg.get("default_countdown_seconds", 10)
    enable_stop_button = oyanmo_cfg.get("enable_stop_button", True)

    # メイン埋め込み
    embed = discord.Embed(
        title="おやんも コマンド実行",
        description=f"🔹 `{interaction.user.display_name}` が `/おやんも {target_member.display_name}` を実行しました。",
        color=0x5865F2
    )

    # STOP ボタン ON/OFF を設定に合わせる
    view = StopButtonView(guild_id, target_member.id, interaction.user.id) if enable_stop_button else None
    await countdown_msg.edit(embed=embed, view=view)

    countdown_active[target_member.id] = True

    # ★ カウントダウン実行
    for i in range(countdown_seconds, -1, -1):

        if not countdown_active.get(target_member.id, False):
            embed.description = f"⏹ `{target_member.display_name}` の移動を中止しました！"
            embed.color = 0xFF4500
            await countdown_msg.edit(embed=embed, view=None)
            return

        embed.description = f"⏳ `{target_member.display_name}` を移動させます: `{i}`"
        await countdown_msg.edit(embed=embed)
        await asyncio.sleep(1)

    # 最終確認、途中停止されてたら抜ける
    if not countdown_active.get(target_member.id, False):
        embed.description = f"⏹ `{target_member.display_name}` の移動を中止しました！"
        embed.color = 0xFF4500
        await countdown_msg.edit(embed=embed, view=None)
        return

    # 実際に移動
    await target_member.move_to(target_channel)

    # ★ 成功メッセージ → guild_id を渡す！
    embed.description = get_random_success_message(guild_id, target_member.display_name)
    embed.color = 0x32CD32

    await countdown_msg.edit(embed=embed, view=None)
    countdown_active[target_member.id] = False
