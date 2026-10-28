# cogs/rainbowl_server_stats.py
"""
rainbowl：サーバー統計を鍵付きVCのチャンネル名で表示する。

  🔒 VC接続数: nn
  🔒 会員数: nn
  🔒 男性数: nn
  🔒 女性数: nn

- 会員数：「新人」または「member」ロールを持つ人（Bot除く）
- 男性数・女性数：会員のうち、性別ロール（profile.gender_roles）を持つ人
- VC接続数：サーバー内のVC・ステージに接続している人（Bot除く）

/setup_server_stats でカテゴリを指定すると、Botが4つのVCを作成し
（@everyoneは接続不可）、以降10分ごとに名前を更新する。
Discordのチャンネル名変更は1チャンネルにつき10分に2回までのため、
これより短い間隔にはしない。値が変わっていなければ名前を変更しない。

設定は zero_bot_guild_config の server_stats 名前空間：
    {"category_id": "...", "channels": {"vc": "...", "members": "...",
                                       "male": "...", "female": "..."}}
"""

import asyncio
from typing import Dict, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from data.guild_config_store import GuildConfigStore
from services.rainbowl_config_service import (
    RainbowlConfigError,
    get_rainbowl_config,
)


UPDATE_INTERVAL_MINUTES = 10

# (キー, 表示名)。この順でカテゴリ内に並べる
STAT_ITEMS = [
    ("vc", "VC接続数"),
    ("members", "会員数"),
    ("male", "男性数"),
    ("female", "女性数"),
]

_guild_config_store = GuildConfigStore()


def _stat_channel_name(label: str, value: int) -> str:
    return f"{label}: {value}"


async def _load_stats_config(guild_id: int) -> Optional[Dict]:
    config = await asyncio.to_thread(_guild_config_store.get_config, guild_id)
    raw = (config or {}).get("server_stats")
    return raw if isinstance(raw, dict) else None


async def _gender_role_ids(guild_id: int) -> Dict[str, Optional[int]]:
    config = await asyncio.to_thread(_guild_config_store.get_config, guild_id)
    gender_roles = ((config or {}).get("profile") or {}).get("gender_roles") or {}
    result: Dict[str, Optional[int]] = {}
    for key in ("male", "female"):
        try:
            result[key] = int(gender_roles[key]["role_id"])
        except (KeyError, TypeError, ValueError):
            result[key] = None
    return result


async def collect_stats(guild: discord.Guild) -> Dict[str, int]:
    """表示する4つの値を数える。rainbowl設定が無い場合はRainbowlConfigError。"""
    rb_config = await get_rainbowl_config(guild.id)
    gender_role_ids = await _gender_role_ids(guild.id)
    member_role_ids = {rb_config.newcomer_role_id, rb_config.member_role_id}

    members = 0
    gender_counts = {"male": 0, "female": 0}

    for member in guild.members:
        if member.bot:
            continue
        role_ids = {role.id for role in member.roles}
        if not role_ids & member_role_ids:
            continue
        members += 1
        for key, role_id in gender_role_ids.items():
            if role_id is not None and role_id in role_ids:
                gender_counts[key] += 1

    vc = sum(
        1
        for channel in [*guild.voice_channels, *guild.stage_channels]
        for member in channel.members
        if not member.bot
    )

    return {"vc": vc, "members": members, **gender_counts}


class RainbowlServerStats(commands.Cog):
    """rainbowl：サーバー統計の表示（鍵付きVCのチャンネル名）"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.update_loop.start()

    def cog_unload(self) -> None:
        self.update_loop.cancel()

    async def _update_guild(self, guild: discord.Guild) -> None:
        stats_config = await _load_stats_config(guild.id)
        if not stats_config:
            return

        channel_ids = stats_config.get("channels") or {}
        stats = await collect_stats(guild)

        for key, label in STAT_ITEMS:
            try:
                channel = guild.get_channel(int(channel_ids[key]))
            except (KeyError, TypeError, ValueError):
                channel = None

            if not isinstance(channel, discord.VoiceChannel):
                print(
                    "[server_stats] 表示用VCが見つかりません"
                    f" guild_id={guild.id} key={key}"
                )
                continue

            name = _stat_channel_name(label, stats[key])
            if channel.name == name:
                continue

            try:
                await channel.edit(name=name, reason="サーバー統計の更新")
            except discord.HTTPException as exc:
                print(
                    "[server_stats] 表示用VCの名前変更に失敗しました"
                    f" guild_id={guild.id} key={key} error={exc}"
                )

    @tasks.loop(minutes=UPDATE_INTERVAL_MINUTES)
    async def update_loop(self) -> None:
        for guild in list(self.bot.guilds):
            try:
                await self._update_guild(guild)
            except RainbowlConfigError:
                continue
            except Exception as exc:
                print(
                    "[server_stats] 更新に失敗しました"
                    f" guild_id={guild.id} error={exc}"
                )

    @update_loop.before_loop
    async def before_update_loop(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="setup_server_stats",
        description="サーバー統計（VC接続数・会員数・男性数・女性数）の表示用VCを作成します（管理者専用）",
    )
    @app_commands.describe(
        category="表示用VCを作成するカテゴリー（再実行すると前回のVCは削除して作り直します）",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_server_stats(
        self,
        interaction: discord.Interaction,
        category: discord.CategoryChannel,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild

        try:
            stats = await collect_stats(guild)
        except RainbowlConfigError:
            await interaction.followup.send(
                "このサーバーにはrainbowlの設定が無いため、統計を表示できません。",
                ephemeral=True,
            )
            return

        # 再実行時は前回作ったVCを消して作り直す
        old_config = await _load_stats_config(guild.id)
        for raw_id in ((old_config or {}).get("channels") or {}).values():
            try:
                old_channel = guild.get_channel(int(raw_id))
            except (TypeError, ValueError):
                old_channel = None
            if old_channel is not None:
                try:
                    await old_channel.delete(reason="サーバー統計の作り直し")
                except discord.HTTPException:
                    pass

        # カテゴリの権限（誰に見えるか）を引き継いだうえで、
        # @everyoneの接続だけ拒否する（鍵付きVCとして表示される）
        overwrites = dict(category.overwrites)
        everyone_ow = overwrites.get(
            guild.default_role, discord.PermissionOverwrite()
        )
        everyone_ow.update(connect=False)
        overwrites[guild.default_role] = everyone_ow
        overwrites[guild.me] = discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            manage_channels=True,
        )

        channel_ids: Dict[str, str] = {}
        for key, label in STAT_ITEMS:
            channel = await guild.create_voice_channel(
                name=_stat_channel_name(label, stats[key]),
                category=category,
                overwrites=overwrites,
                reason="サーバー統計の表示用VC",
            )
            channel_ids[key] = str(channel.id)

        await asyncio.to_thread(
            _guild_config_store.set_namespace,
            guild.id,
            "server_stats",
            {"category_id": str(category.id), "channels": channel_ids},
        )

        await interaction.followup.send(
            f"{category.mention} にサーバー統計の表示用VCを作成しました。"
            f"{UPDATE_INTERVAL_MINUTES}分ごとに更新されます。",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RainbowlServerStats(bot))
