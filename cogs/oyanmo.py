import discord
from discord import app_commands
from discord.ext import commands

from typing import Optional  # ← ★ 追加

from utils.helpers import voice_users_autocomplete
from utils.countdown import countdown_procedure, countdown_active
from utils.messages import get_random_success_message

# ★ DB からギルド設定を取るための Store
from data.guild_config_store import GuildConfigStore

# グローバルに1個だけ作って使い回し
guild_config_store = GuildConfigStore()


class OyanmoCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _get_oyanmo_config(self, guild_id: int) -> dict:
        """
        ギルドごとのおやんも設定を DynamoDB から取得。
        何もなければ空 dict を返す。
        """
        config = guild_config_store.get_config(guild_id) or {}
        oyanmo_cfg = config.get("oyanmo") or {}
        return oyanmo_cfg

    def _get_target_voice_channel(
        self, guild: discord.Guild
    ) -> Optional[discord.VoiceChannel]: 
        """
        oyanmo 設定から target_voice_channel_id を取得して、
        実際の VoiceChannel オブジェクトに変換する。
        """
        oyanmo_cfg = self._get_oyanmo_config(guild.id)
        raw_id = oyanmo_cfg.get("target_voice_channel_id")

        if not raw_id:
            return None

        try:
            chan_id = int(raw_id)
        except (TypeError, ValueError):
            return None

        ch = guild.get_channel(chan_id) or self.bot.get_channel(chan_id)
        if isinstance(ch, discord.VoiceChannel):
            return ch
        return None

    def _is_allowed_user(self, interaction: discord.Interaction) -> bool:
        """
        allowed_role_ids が設定されている場合、
        そのいずれかのロールを持っているユーザーだけ /おやんも を実行可能にする。
        未設定 or 空リストなら誰でもOK。
        """
        guild = interaction.guild
        user = interaction.user

        if guild is None or not isinstance(user, discord.Member):
            return False

        oyanmo_cfg = self._get_oyanmo_config(guild.id)
        raw_roles = oyanmo_cfg.get("allowed_role_ids") or []

        # 設定が空なら制限なし
        if not raw_roles:
            return True

        try:
            allowed_role_ids = {int(r) for r in raw_roles}
        except (TypeError, ValueError):
            # 変な値が入っていたら、とりあえず全員許可にしておく
            return True

        user_role_ids = {role.id for role in user.roles}
        return bool(allowed_role_ids & user_role_ids)

    @app_commands.command(
        name="おやんも",
        description="指定したユーザーを寝落ち部屋に移動させます",
    )
    @app_commands.autocomplete(username=voice_users_autocomplete)
    async def おやんも(
        self,
        interaction: discord.Interaction,
        username: str,
        countdown: bool = False,
    ):
        await interaction.response.defer()

        guild = interaction.guild
        if guild is None:
            await interaction.followup.send(
                "サーバー情報を取得できません。", ephemeral=True
            )
            return

        # 権限チェック（allowed_role_ids）
        if not self._is_allowed_user(interaction):
            await interaction.followup.send(
                "❌ このサーバーでは、あなたは `/おやんも` を実行できません。", ephemeral=True
            )
            return

        # 対象ユーザー検索
        target_member = discord.utils.find(
            lambda m: m.display_name == username, guild.members
        )
        if target_member is None or target_member.voice is None:
            await interaction.followup.send(
                f"❌ `{username}` はボイスチャンネルにいません。"
            )
            return

        # ★ ここが DB からの取得に変わったところ
        target_channel = self._get_target_voice_channel(guild)
        if not isinstance(target_channel, discord.VoiceChannel):
            await interaction.followup.send(
                "❌ おやんも先のボイスチャンネルが設定されていません。\n"
                "管理者さんに `target_voice_channel_id` の設定をお願いしてください。",
                ephemeral=True,
            )
            return

        # 初期表示
        embed = discord.Embed(
            title="おやんも実行",
            description=f"`{target_member.display_name}` を寝落ち部屋へ移動させます。",
            color=0x5865F2,
        )
        response_message = await interaction.followup.send(embed=embed, wait=True)

        # カウントダウン実行
        if countdown:
            await countdown_procedure(
                interaction, target_member, target_channel, response_message
            )
        else:
            await target_member.move_to(target_channel)
            embed.description = get_random_success_message(
                guild.id,
                target_member.display_name,
            )
            embed.color = 0x32CD32
            await response_message.edit(embed=embed, view=None)


async def setup(bot):
    await bot.add_cog(OyanmoCog(bot))
