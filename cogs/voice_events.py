import discord
import random
import datetime
import pytz
import asyncio
import logging
import os

from discord.ext import commands

from utils.helpers import normalize_text_channel_name
from utils.channel_manager import ChannelManager
from data.guild_config_store import GuildConfigStore
from utils.helpers import load_profile_messages, save_profile_messages
from config import debug_log

# JST設定
jst = pytz.timezone("Asia/Tokyo")

# DynamoDB guild_config
config_store = GuildConfigStore()

# ログ設定（省略）


class VoiceEventsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.channel_manager = ChannelManager(bot)
        self.join_message_tracking = {}  # {user_id: (channel_id, message_id)}
        self.profile_message_map = load_profile_messages()

    # 🔹 設定値取得の共通メソッド
    def _get_config(self, guild_id):
        return config_store.get_config(guild_id) or {}

    # 🔹 除外カテゴリ判定
    def is_excluded(self, channel: discord.abc.GuildChannel) -> bool:
        if channel is None or channel.category is None:
            return False

        cfg = self._get_config(channel.guild.id)
        profile_cfg = cfg.get("profile") or {}
        raw_ids = profile_cfg.get("excluded_category_ids") or []

        excluded_ids = {int(cid) for cid in raw_ids if str(cid).isdigit()}
        return channel.category.id in excluded_ids

    # 🔹 退出後に削除しないカテゴリ
    def is_delete_excluded_category(self, category_id, guild_id):
        cfg = self._get_config(guild_id)
        profile_cfg = cfg.get("profile") or {}
        raw_ids = profile_cfg.get("leave_message_delete_excluded_category_ids") or []

        excluded_ids = {int(cid) for cid in raw_ids if str(cid).isdigit()}
        return category_id in excluded_ids

    # 🔹 プロフ探索チャンネル
    def get_profile_source_channels(self, guild_id):
        cfg = self._get_config(guild_id)
        profile_cfg = cfg.get("profile") or {}
        return [int(cid) for cid in profile_cfg.get("profile_source_channel_ids") or []]

    # 🔹 性別ロール設定
    def get_gender_role_colors(self, guild_id):
        cfg = self._get_config(guild_id)
        profile_cfg = cfg.get("profile") or {}

        gender_roles = profile_cfg.get("gender_roles") or {}
        # 例：
        # {
        #   "male": {"role_id": 11111, "color": 0x206694},
        #   "female": {"role_id": 22222, "color": 0xff00ff}
        # }
        return gender_roles

    # ===============================
    #   Voice update メイン処理
    # ===============================
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        guild = member.guild
        guild_id = guild.id

        # =========
        #  退室処理
        # =========
        if before.channel and before.channel != after.channel:
            if not self.is_excluded(before.channel):
                text_channel = await self.channel_manager.get_or_create_text_channel(guild, before.channel)

                embed = discord.Embed(
                    description=f"**{member.display_name}**（ID:`{member.id}`）が **{before.channel.name}** から退出しました。",
                    color=0xE74C3C
                )
                embed.set_author(name=f"{member.display_name} さんの退出", icon_url=member.display_avatar.url)
                embed.set_footer(text=datetime.datetime.now(jst).strftime("%Y-%m-%d %H:%M:%S"))

                await text_channel.send(embed=embed)

                # 退出後の残り人数
                member_count = len(before.channel.members)

                # 0人ならメッセージ整理
                if member_count == 0:
                    category_id = before.channel.category_id

                    if not self.is_delete_excluded_category(category_id, guild_id):
                        await self.delete_all_messages_from_channel(before.channel)
                    else:
                        debug_log(f"[SKIP DELETE] {before.channel.name} は削除しないカテゴリ")

                # 🔽 プロフメッセージ削除
                profile_data = self.profile_message_map.get(str(member.id))
                if profile_data:
                    try:
                        channel = self.bot.get_channel(int(profile_data["channel_id"]))
                        msg = await channel.fetch_message(int(profile_data["message_id"]))
                        await msg.delete()

                        del self.profile_message_map[str(member.id)]
                        save_profile_messages(self.profile_message_map)

                    except Exception as e:
                        debug_log(f"[DELETE ERROR] プロフ削除失敗: {e}")

        # =========
        #  入室処理
        # =========
        if after.channel and before.channel != after.channel:
            if not self.is_excluded(after.channel):
                text_channel = await self.channel_manager.get_or_create_text_channel(guild, after.channel)

                embed = discord.Embed(
                    description=f"**{member.display_name}** が **{after.channel.name}** に入室しました。",
                    color=0x2ECC71
                )
                embed.set_author(name=f"{member.display_name} さんの入室", icon_url=member.display_avatar.url)
                embed.set_footer(text=datetime.datetime.now(jst).strftime("%Y-%m-%d %H:%M:%S"))

                sent_msg = await text_channel.send(embed=embed)
                self.join_message_tracking[member.id] = (text_channel.id, sent_msg.id)

                # 🔽 プロフリンク投稿
                await self.post_user_recent_message_link(member, after.channel)

                # 前チャンネルが 0人なら削除
                if before.channel and len(before.channel.members) == 0:
                    if not self.is_delete_excluded_category(before.channel.category_id, guild_id):
                        await self.delete_all_messages_from_channel(before.channel)

    # ===============================
    #  プロフィールリンク探索
    # ===============================
    async def find_latest_message_link(self, member):
        channels = self.get_profile_source_channels(member.guild.id)

        for cid in channels:
            ch = self.bot.get_channel(cid)
            if not ch:
                continue

            async for msg in ch.history(limit=100):
                if msg.author.id == member.id:
                    return f"https://discord.com/channels/{msg.guild.id}/{msg.channel.id}/{msg.id}"

        return None

    async def post_user_recent_message_link(self, member, target_channel):
        link = await self.find_latest_message_link(member)
        if not link:
            return

        display_name = member.nick or member.display_name

        # 🔹 性別ロール設定
        gender_cfg = self.get_gender_role_colors(member.guild.id)

        embed_color = 0x2ECC71  # default

        # male
        male = gender_cfg.get("male")
        if male and int(male["role_id"]) in [r.id for r in member.roles]:
            embed_color = int(male["color"])

        # female
        female = gender_cfg.get("female")
        if female and int(female["role_id"]) in [r.id for r in member.roles]:
            embed_color = int(female["color"])

        # メッセージ作成
        intro = random.choice([
            "みてみて、このひとこんなひと",
            "ほらほら、きたよ！挨拶して！！",
            "自己紹介はこちら！",
            "気になる？ クリックして！"
        ])

        embed = discord.Embed(
            title=intro,
            description=f"[ ▶ プロフィールを見る ]({link})",
            color=embed_color
        )
        embed.set_author(name=f"{display_name} が入室したよ！", icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)

        sent = await target_channel.send(embed=embed)

        # 保存
        self.profile_message_map[str(member.id)] = {
            "channel_id": str(target_channel.id),
            "message_id": str(sent.id)
        }
        save_profile_messages(self.profile_message_map)

    # ===============================
    #  メッセージ全削除
    # ===============================
    async def delete_all_messages_from_channel(self, target_channel):
        while True:
            msgs = [m async for m in target_channel.history(limit=100)]
            if not msgs:
                break

            try:
                await target_channel.delete_messages(msgs)
                await asyncio.sleep(1)
            except Exception as e:
                print(f"[ERROR] bulk_delete: {e}")
                break


async def setup(bot):
    await bot.add_cog(VoiceEventsCog(bot))
