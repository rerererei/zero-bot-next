# cogs/voice_leveling.py

from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta

from data.store import (
    add_voice_xp,
    get_guild_user_stats,
    get_voice_meta,      # 統計メタ情報の取得（JsonStore 経由）
    update_voice_meta,   # 統計メタ情報の更新（JsonStore 経由）
)


# ===== XP計算ロジック =====
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


# タイムゾーン（JSTで集計したい場合）
JST = timezone(timedelta(hours=9))


class VoiceLeveling(commands.Cog):
    """VCレベリング & 統計"""

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
        """
        60秒ごとにVC参加者にXP & 統計を付与する。
        ★ このループでは「1分ごと」に処理しているので、
           統計値（total_time など）はすべて『分』でカウントする。
        """
        TICK_SECONDS = 60
        TICK_MINUTES = TICK_SECONDS / 60.0  # = 1.0 分

        for guild in self.bot.guilds:
            for vc in guild.voice_channels:
                # Bot 以外の参加メンバーだけを見る
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

                    # ===== 統計メタ情報の取得（JsonStore経由）=====
                    meta = get_voice_meta(guild.id, member.id)
                    # ここで扱う値はすべて「分」単位で統一する

                    # --- 総滞在時間（分） ---
                    meta["total_time"] = float(meta.get("total_time", 0.0)) + TICK_MINUTES

                    # --- 人数帯ごとの時間（分） ---
                    if member_count == 1:
                        key = "solo_time"
                    elif 2 <= member_count <= 3:
                        key = "small_group_time"
                    elif 4 <= member_count <= 6:
                        key = "mid_group_time"
                    else:
                        key = "big_group_time"

                    meta[key] = float(meta.get(key, 0.0)) + TICK_MINUTES

                    # --- ミュート状態の時間（分） ---
                    if is_muted:
                        meta["muted_time"] = float(meta.get("muted_time", 0.0)) + TICK_MINUTES

                    # --- 同席人数の最大値 ---
                    meta["max_member_count"] = max(
                        int(meta.get("max_member_count", 0)),
                        member_count,
                    )

                    # --- 時間帯バケット（hour_buckets: 0〜23時, 単位: 分） ---
                    hour_buckets = meta.get("hour_buckets")
                    if not isinstance(hour_buckets, list) or len(hour_buckets) != 24:
                        hour_buckets = [0.0] * 24

                    now = datetime.now(JST)
                    current_hour = now.hour  # 0〜23

                    hour_buckets[current_hour] += TICK_MINUTES
                    meta["hour_buckets"] = hour_buckets

                    # --- 一緒にいた相手ごとの滞在時間（pair_time: 単位: 分） ---
                    pair_time = meta.get("pair_time")
                    if not isinstance(pair_time, dict):
                        pair_time = {}

                    # 自分以外のメンバー全員分を加算
                    others = [m for m in members if m.id != member.id]
                    for other in others:
                        oid = str(other.id)  # JSONに安全に載せるため文字列キーにしておく
                        prev = float(pair_time.get(oid, 0.0))
                        pair_time[oid] = prev + TICK_MINUTES

                    meta["pair_time"] = pair_time

                    # --- 変更をストアに保存 ---
                    update_voice_meta(guild.id, member.id, meta)

        # 各ギルドごとの保存済みユーザー数を集計
        total_users = 0
        for guild in self.bot.guilds:
            stats = get_guild_user_stats(guild.id)  # { user_id: {voice_xp, text_xp} }
            total_users += len(stats)

    @voice_snapshot_loop.before_loop
    async def before_voice_snapshot_loop(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceLeveling(bot))
