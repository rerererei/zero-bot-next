import discord
from discord import app_commands
import re

from config import debug_log
from data.guild_config_store import GuildConfigStore

# DynamoDB ギルド設定
config_store = GuildConfigStore()


def normalize_voice_channel_name(name: str) -> str:
    """ボイスチャンネル名を比較用に正規化"""
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def normalize_text_channel_name(name: str) -> str:
    """テキストチャンネル名を比較用に正規化"""
    name = re.sub(r'\s+', '-', name.strip())
    name = re.sub(r'-+', '-', name)
    return name.strip('-')


async def voice_users_autocomplete(interaction: discord.Interaction, current: str):
    """ボイスチャンネルのユーザーをオートコンプリート（DB対応版）"""

    guild = interaction.guild
    if guild is None:
        debug_log("サーバー情報なし")
        return []

    guild_id = guild.id

    # 🔹 DB からギルド設定を取得
    cfg = config_store.get_config(guild_id) or {}
    profile_cfg = cfg.get("profile") or {}

    # DB に未設定なら空扱い
    excluded_voice_channels = profile_cfg.get("excluded_voice_channel_ids", [])
    excluded_voice_channels = [int(c) for c in excluded_voice_channels]  # 文字列対応

    current_lower = (current or "").lower()
    voice_members = []

    debug_log(f"[AUTO] 除外VC = {excluded_voice_channels}")

    for vc in guild.voice_channels:

        # 🔹 DB で除外指定された VC をスキップ
        if vc.id in excluded_voice_channels:
            debug_log(f"[AUTO] 除外VCスキップ: {vc.name} ({vc.id})")
            continue

        for member in vc.members:
            if current_lower in member.display_name.lower():
                voice_members.append(member.display_name)

    debug_log(f"[AUTO] 候補 = {voice_members[:25]}")

    return [app_commands.Choice(name=name, value=name) for name in voice_members[:25]]
