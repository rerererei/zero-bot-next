from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta 

from data.store import (
    add_voice_xp,
    get_guild_user_stats,
    get_voice_meta,       # ★ 追加
    update_voice_meta,    # ★ 追加
)

JST = timezone(timedelta(hours=9))

def calc_voice_xp_per_minute(member_count: int, is_muted: bool) -> float:
    """
    1分あたりのボイスXPを計算する。
    式: XP = 0.3 × 人数ボーナス × ミュート倍率
    """
    base = 0.3

    # 人数ボーナス
    if member_count <= 1:
        bonus = 0.8
    elif member_count <= 3:
        bonus = 1.2
    elif member_count <= 6:
        bonus = 1.5
    else:
        bonus = 2.0

    # ミュート倍率
    mute_factor = 0.5 if is_muted else 1.0

    return base * bonus * mute_factor


class VoiceLeveling(commands.Cog):
    """VCレベリング"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # VCスナップショットループ開始
        self.voice_snapshot_loop.start()
        print("[VoiceLeveling] voice_snapshot_loop started")

    def cog_unload(self):
        # Cogアンロード時にループ停止
        self.voice_snapshot_loop.cancel()

    @tasks.loop(seconds=60)
    async def voice_snapshot_loop(self):
        """60秒ごとにVC参加者にXP&統計を付与"""
        TICK_SECONDS = 60  # このループ間隔

        for guild in self.bot.guilds:
            for vc in guild.voice_channels:
                members = [m for m in vc.members if not m.bot]
                if not members:
                    continue

                member_count = len(members)

                for member in members:
                    state = member.voice
                    if state is None:
                        continue

                    is_muted = bool(
                        state.self_mute
                        or state.self_deaf
                        or state.mute
                        or state.deaf
                    )

                    # ===== XP付与 =====
                    xp = calc_voice_xp_per_minute(member_count, is_muted)
                    add_voice_xp(guild.id, member.id, xp)

                    # ===== 統計カウント（JsonStore 側の meta を直接更新）=====
                    meta = get_voice_meta(guild.id, member.id)

                    # 総滞在時間
                    meta["total_time"] = meta.get("total_time", 0) + TICK_SECONDS

                    # 人数帯ごとの時間
                    if member_count == 1:
                        meta["solo_time"] = meta.get("solo_time", 0) + TICK_SECONDS
                    elif 2 <= member_count <= 3:
                        meta["small_group_time"] = meta.get("small_group_time", 0) + TICK_SECONDS
                    elif 4 <= member_count <= 6:
                        meta["mid_group_time"] = meta.get("mid_group_time", 0) + TICK_SECONDS
                    else:
                        meta["big_group_time"] = meta.get("big_group_time", 0) + TICK_SECONDS

                    # ミュート時間
                    if is_muted:
                        meta["muted_time"] = meta.get("muted_time", 0) + TICK_SECONDS

                    # 同席人数の最大値
                    meta["max_member_count"] = max(
                        meta.get("max_member_count", 0),
                        member_count,
                    )

                    # ★★★ ここから「時間帯バケツ」ロジック ★★★
                    TICK_SECONDS = 60
                    TICK_MINUTES = TICK_SECONDS / 60  # 今は 1.0 分

                    hour_buckets = meta.get("hour_buckets")
                    if not isinstance(hour_buckets, list) or len(hour_buckets) != 24:
                        hour_buckets = [0.0] * 24

                    now = datetime.now(JST)
                    current_hour = now.hour  # 0〜23

                    # ★ ここを『秒』ではなく『分』でカウント
                    hour_buckets[current_hour] += TICK_MINUTES   # 1分ずつ増えていく
                    meta["hour_buckets"] = hour_buckets
                    # ★★★ ここまで ★★★

                    # ★ 変更を保存（Json に書き戻し）
                    update_voice_meta(guild.id, member.id, meta)

        # 各ギルドごとの保存済みユーザー数を集計（Json/Dynamo でも同じ）
        total_users = 0
        for guild in self.bot.guilds:
            stats = get_guild_user_stats(guild.id)  # { user_id: {voice_xp, text_xp} }
            total_users += len(stats)

        print(f"[voice_snapshot_loop] 更新完了: ユーザー数={total_users}")

    @voice_snapshot_loop.before_loop
    async def before_voice_snapshot_loop(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceLeveling(bot))

