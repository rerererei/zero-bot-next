# services/rainbowl_activity_review_service.py
"""
rainbowl：月次活動整理（在籍確認）。rainbowl_server_handover.md 11章・12章。

流れ：
1. 毎月1日（日次バッチ）に前月分を集計し、基準未達者の在籍確認サイクルを開始
   （確認用ロール付与・本人専用チャンネル作成・運営チャンネルへ対象者一覧＋「残留」ボタン）
2. 運営が「残留」ボタンを押すと残留確定（ロール解除・本人へ通知）
3. 確認期限（集計日の7日後0:00）の日次バッチで、残留確定していない人をキック。
   /kick_is_enabled でその月を無効にしていた場合は、1の集計自体を行わない
   （サイクルが始まらないので8日のキックも発生しない）。
   残留・キック・見送りいずれも、本人専用チャンネルはこの時点で削除する。

活動スコア = 対象月のrainbowl VC XP + テキストXP（既存のXP計算式をそのまま使う）。
"""

import asyncio
import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import discord

from data.guild_config_store import GuildConfigStore
from data.rainbowl.activity_review_store import ActivityReviewStore
from data.rainbowl.applicants_store import RainbowlStore
from data.rainbowl.member_state_store import MemberStateStore
from data import text_daily_store, voice_daily_store
from services import rainbowl_texts
from services.rainbowl_config_service import (
    RainbowlConfigError,
    RainbowlGuildConfig,
    get_rainbowl_config,
    is_admin_like,
)
from utils.helpers import normalize_text_channel_name


JST = timezone(timedelta(hours=9))

# 集計処理がこの時間以上IN_PROGRESSのまま残っていたら異常終了とみなして再実行する
BATCH_STALE_AFTER = timedelta(hours=1)

RETAIN_CUSTOM_ID_PREFIX = "rainbowl_activity_retain"

_guild_config_store = GuildConfigStore()
_review_store = ActivityReviewStore()
_member_store = MemberStateStore()
_applicants_store = RainbowlStore()


# ========================================
# 設定（guild_configのactivity_review名前空間）
# ========================================

@dataclass(frozen=True)
class ActivityReviewConfig:
    review_role_id: int
    staff_list_channel_id: int
    review_category_id: int
    exempt_role_ids: Tuple[int, ...]
    min_score: float
    confirm_days: int
    newcomer_grace_days: int
    start_month: str


async def get_activity_review_config(
    guild_id: int,
) -> Optional[ActivityReviewConfig]:
    """
    activity_review名前空間を読む。未設定・不正な場合はNone
    （月次活動整理をそのギルドでは実行しない）。
    """
    guild_config = await asyncio.to_thread(
        _guild_config_store.get_config,
        guild_id,
    )
    raw = (guild_config or {}).get("activity_review")

    if not isinstance(raw, dict):
        return None

    try:
        return ActivityReviewConfig(
            review_role_id=int(raw["review_role_id"]),
            staff_list_channel_id=int(raw["staff_list_channel_id"]),
            review_category_id=int(raw["review_category_id"]),
            exempt_role_ids=tuple(
                int(x) for x in raw.get("exempt_role_ids") or []
            ),
            min_score=float(raw.get("min_score", 60)),
            confirm_days=int(raw.get("confirm_days", 7)),
            newcomer_grace_days=int(raw.get("newcomer_grace_days", 14)),
            start_month=str(raw.get("start_month", "2026-11")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        print(
            "[activity_review] activity_review設定が不正です:"
            f" guild_id={guild_id} error={exc}"
        )
        return None


# ========================================
# 月・日付ユーティリティ
# ========================================

MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def previous_month(month: str) -> str:
    year, mon = map(int, month.split("-"))
    if mon == 1:
        return f"{year - 1}-12"
    return f"{year}-{mon - 1:02d}"


def month_of(day: date) -> str:
    return f"{day.year}-{day.month:02d}"


def month_range(month: str) -> Tuple[date, date]:
    """対象月の初日と末日（両端含む）。"""
    year, mon = map(int, month.split("-"))
    first = date(year, mon, 1)
    next_first = (
        date(year + 1, 1, 1) if mon == 12 else date(year, mon + 1, 1)
    )
    return first, next_first - timedelta(days=1)


def month_label(month: str) -> str:
    year, mon = map(int, month.split("-"))
    return f"{year}年{mon}月"


def deadline_label(deadline: datetime) -> str:
    """期限（0:00）を「12月7日 23:59まで」の形で表示する。"""
    last = deadline.astimezone(JST) - timedelta(minutes=1)
    return f"{last.month}月{last.day}日 {last:%H:%M}まで"


def default_kick_setting_month(now: datetime) -> str:
    """
    /kick_is_enabled で対象月を省略した場合の対象月＝今月
    （翌月1日の集計・8日のキックが対象）。
    設定は毎月9日〜月末に行う運用（コマンド側ではチェックしない）。
    """
    return month_of(now.date())


async def is_kick_enabled(
    guild_id: int,
    target_month: str,
) -> bool:
    """未設定の月はキック有効。"""
    setting = await asyncio.to_thread(
        _review_store.get_kick_setting,
        guild_id,
        target_month,
    )
    if setting is None:
        return True
    return bool(setting.get("enabled", True))


async def set_kick_enabled(
    guild_id: int,
    target_month: str,
    enabled: bool,
    updated_by: int,
) -> None:
    await asyncio.to_thread(
        _review_store.set_kick_setting,
        guild_id,
        target_month,
        enabled,
        updated_by,
        datetime.now(JST).isoformat(),
    )


# ========================================
# 「残留」ボタン（永続・ユーザーごと）
# ========================================

class RetainButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=(
        RETAIN_CUSTOM_ID_PREFIX
        + r":(?P<user_id>[0-9]+):(?P<month>[0-9]{4}-[0-9]{2})"
    ),
):
    """運営チャンネルの対象者一覧に付ける「残留」ボタン。"""

    def __init__(self, user_id: int, target_month: str):
        super().__init__(
            discord.ui.Button(
                label="残留",
                style=discord.ButtonStyle.success,
                custom_id=(
                    f"{RETAIN_CUSTOM_ID_PREFIX}:{user_id}:{target_month}"
                ),
            )
        )
        self.user_id = user_id
        self.target_month = target_month

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
    ):
        return cls(int(match["user_id"]), match["month"])

    async def callback(self, interaction: discord.Interaction) -> None:
        await process_retain_button(
            interaction,
            self.user_id,
            self.target_month,
        )


def _retain_view(user_id: int, target_month: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(RetainButton(user_id, target_month))
    return view


def _closed_view(label: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            label=label,
            style=discord.ButtonStyle.secondary,
            disabled=True,
        )
    )
    return view


# ========================================
# 1. 月次集計（毎月1日）
# ========================================

def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _newcomer_at(applicant: Dict[str, Any]) -> Optional[datetime]:
    """
    「新人」になった日時。newcomer_at導入前に新人になった人は
    status=NEWCOMERのupdated_at（set_newcomerが最後の更新）で代用する。
    """
    newcomer_at = _parse_iso(applicant.get("newcomer_at"))
    if newcomer_at is not None:
        return newcomer_at
    if applicant.get("status") == "NEWCOMER":
        return _parse_iso(applicant.get("updated_at"))
    return None


def _collect_scores(
    guild_id: int,
    month: str,
) -> Dict[int, Tuple[float, float]]:
    """{user_id: (text_xp, vc_xp)}（同期処理。to_threadで呼ぶ）"""
    date_from, date_to = month_range(month)
    text_totals = text_daily_store.get_guild_total_in_range(
        guild_id, date_from, date_to
    )
    vc_totals = voice_daily_store.get_guild_xp_in_range(
        guild_id, date_from, date_to
    )

    scores: Dict[int, Tuple[float, float]] = {}
    for uid in set(text_totals) | set(vc_totals):
        scores[uid] = (
            float(text_totals.get(uid, {}).get("xp_total", 0.0)),
            float(vc_totals.get(uid, 0.0)),
        )
    return scores


RESULT_LABELS = {
    "TARGET": "現時点で基準未達",
    "OK": "現時点で基準達成",
    "STAFF": "除外：運営・管理者",
    "EXEMPT_ROLE": "除外：特別除外ロール",
    "EXEMPT": "除外：個別除外登録",
    "PAUSED": "除外：休止申請中",
    "GRACE": "除外：新人になって日が浅い",
}


@dataclass
class MemberEvaluation:
    member: discord.Member
    text_xp: float
    vc_xp: float
    joined: Optional[datetime]
    result: str  # RESULT_LABELSのキー

    @property
    def score(self) -> float:
        return self.text_xp + self.vc_xp


async def evaluate_members(
    guild: discord.Guild,
    rb_config: RainbowlGuildConfig,
    config: ActivityReviewConfig,
    scores: Dict[int, Tuple[float, float]],
    now: datetime,
) -> List[MemberEvaluation]:
    """
    判定対象（「新人」「member」ロールを持つBot以外）の全員について、
    スコアと判定結果を返す。本番集計とプレビューで共通の判定ロジック。
    DBの除外情報（個別除外・休止・入会日）は基準未達の人だけ参照する。
    """
    grace_border = now - timedelta(days=config.newcomer_grace_days)
    target_role_ids = {rb_config.newcomer_role_id, rb_config.member_role_id}
    exempt_role_ids = set(config.exempt_role_ids)

    evaluations: List[MemberEvaluation] = []

    for member in guild.members:
        if member.bot:
            continue

        role_ids = {role.id for role in member.roles}

        if not role_ids & target_role_ids:
            continue

        text_xp, vc_xp = scores.get(member.id, (0.0, 0.0))
        ev = MemberEvaluation(member, text_xp, vc_xp, None, "TARGET")
        evaluations.append(ev)

        if is_admin_like(member, rb_config):
            ev.result = "STAFF"
            continue
        if role_ids & exempt_role_ids:
            ev.result = "EXEMPT_ROLE"
            continue
        if ev.score >= config.min_score:
            ev.result = "OK"
            continue

        state, applicant = await asyncio.gather(
            asyncio.to_thread(_member_store.get_item, guild.id, member.id),
            asyncio.to_thread(
                _applicants_store.get_item, guild.id, member.id
            ),
        )
        ev.joined = _newcomer_at(applicant)

        if (state.get("exempt") or {}).get("is_exempt"):
            ev.result = "EXEMPT"
        elif (state.get("pause") or {}).get("is_paused"):
            ev.result = "PAUSED"
        elif ev.joined is not None and ev.joined > grace_border:
            ev.result = "GRACE"

    return evaluations


async def preview_current_month(
    guild: discord.Guild,
    rb_config: RainbowlGuildConfig,
    config: ActivityReviewConfig,
) -> Tuple[str, List[MemberEvaluation]]:
    """
    今月1日〜実行時点の活動状況を確認する（本番の集計とは無関係。
    DB・ロール・チャンネルには一切書き込まない）。
    (サマリー文, 全員分の判定結果) を返す。基準未達→スコアの低い順。
    """
    now = datetime.now(JST)
    target_month = month_of(now.date())
    scores = await asyncio.to_thread(_collect_scores, guild.id, target_month)
    evaluations = await evaluate_members(
        guild, rb_config, config, scores, now
    )
    evaluations.sort(key=lambda ev: (ev.result != "TARGET", ev.score))

    counts: Dict[str, int] = {}
    for ev in evaluations:
        counts[ev.result] = counts.get(ev.result, 0) + 1

    summary_lines = [
        f"**{month_label(target_month)}の活動状況**"
        f"（{now.month}/1〜{now:%m/%d %H:%M}時点）",
        f"基準 {config.min_score:g}XP／判定対象 {len(evaluations)}名",
    ] + [
        f"・{RESULT_LABELS[key]}：{counts[key]}名"
        for key in RESULT_LABELS
        if counts.get(key)
    ] + [
        "※確認用です。ロール付与・通知・キックなどは一切行っていません。",
    ]

    return "\n".join(summary_lines), evaluations


def format_preview_line(ev: MemberEvaluation) -> str:
    """ページ送り表示用の1行。"""
    joined = f"／新人 {ev.joined:%Y-%m-%d}" if ev.joined else ""
    return (
        f"**{ev.score:.1f}** {ev.member.mention}"
        f"（テキスト {ev.text_xp:.1f}／VC {ev.vc_xp:.1f}{joined}）"
        f" … {RESULT_LABELS[ev.result]}"
    )


def build_preview_csv(evaluations: List[MemberEvaluation]) -> str:
    """CSV出力用。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["判定", "表示名", "ユーザーID", "スコア", "テキスト", "VC", "新人になった日"]
    )
    for ev in evaluations:
        writer.writerow(
            [
                RESULT_LABELS[ev.result],
                ev.member.display_name,
                str(ev.member.id),
                f"{ev.score:.1f}",
                f"{ev.text_xp:.1f}",
                f"{ev.vc_xp:.1f}",
                f"{ev.joined:%Y-%m-%d}" if ev.joined else "",
            ]
        )

    return buffer.getvalue()


async def run_monthly_review(
    guild: discord.Guild,
    rb_config: RainbowlGuildConfig,
    config: ActivityReviewConfig,
) -> Optional[str]:
    """
    前月分の集計を行い、基準未達者の在籍確認サイクルを開始する。
    対象月が開始月より前・既に処理済みの場合はNone（何もしない）。
    """
    now = datetime.now(JST)
    target_month = previous_month(month_of(now.date()))

    if target_month < config.start_month:
        return None

    claimed = await asyncio.to_thread(
        _review_store.claim_monthly_batch,
        guild.id,
        target_month,
        now.isoformat(),
        (now - BATCH_STALE_AFTER).isoformat(),
    )

    if not claimed:
        return None

    if not await is_kick_enabled(guild.id, target_month):
        # キック無効の月は集計自体を行わない（ロール付与・チャンネル作成・通知もしない）
        await _post_staff_summary(
            guild,
            config,
            f"📋 {month_label(target_month)}分の活動確認：キック無効の月のため、集計を行いません。",
        )
        await asyncio.to_thread(
            _review_store.finish_monthly_batch,
            guild.id,
            target_month,
            datetime.now(JST).isoformat(),
        )
        return f"在籍確認 {month_label(target_month)}分 キック無効のため集計なし"

    scores, prev_scores = await asyncio.gather(
        asyncio.to_thread(_collect_scores, guild.id, target_month),
        asyncio.to_thread(
            _collect_scores, guild.id, previous_month(target_month)
        ),
    )

    deadline = datetime.combine(
        now.date() + timedelta(days=config.confirm_days),
        time(0, 0),
        tzinfo=JST,
    )

    started = 0
    failed = 0

    evaluations = await evaluate_members(
        guild, rb_config, config, scores, now
    )

    for ev in evaluations:
        if ev.result != "TARGET":
            continue

        member = ev.member

        try:
            if await _start_review(
                guild,
                member,
                config,
                staff_role_id=rb_config.staff_role_id,
                target_month=target_month,
                text_xp=ev.text_xp,
                vc_xp=ev.vc_xp,
                prev_score=sum(prev_scores.get(member.id, (0.0, 0.0))),
                joined=ev.joined,
                deadline=deadline,
                now=now,
            ):
                started += 1
        except Exception as exc:
            failed += 1
            print(
                "[activity_review] 在籍確認の開始に失敗しました:"
                f" guild_id={guild.id} user_id={member.id} error={exc}"
            )

    if failed:
        # DONEにしない：翌日の日次バッチで再クレームし、途中から再開する
        await _post_staff_summary(
            guild,
            config,
            f"⚠️ {month_label(target_month)}分の活動確認：{failed}名の処理に失敗しました。"
            "翌日の日次バッチ（または /zbadmin run_daily_batch を1時間以上あけて実行）で再試行します。",
        )
        return f"在籍確認 {month_label(target_month)}分 {failed}名失敗（再試行待ち）"

    await _post_staff_summary(
        guild,
        config,
        f"📋 {month_label(target_month)}分の活動確認：対象者 {started}名"
        f"（基準 {config.min_score:g}XP／確認期限 {deadline_label(deadline)}）",
    )

    await asyncio.to_thread(
        _review_store.finish_monthly_batch,
        guild.id,
        target_month,
        datetime.now(JST).isoformat(),
    )

    return f"在籍確認 {month_label(target_month)}分 対象者{started}名"


async def _start_review(
    guild: discord.Guild,
    member: discord.Member,
    config: ActivityReviewConfig,
    *,
    staff_role_id: int,
    target_month: str,
    text_xp: float,
    vc_xp: float,
    prev_score: float,
    joined: Optional[datetime],
    deadline: datetime,
    now: datetime,
) -> bool:
    """
    1人分の在籍確認サイクルを開始する。途中で失敗して再実行された場合も、
    current_reviewに記録済みのチャンネル・運営通知は作り直さない。
    対象月のサイクルが進行中になっていればTrueを返す。
    """
    await asyncio.to_thread(
        _member_store.start_review_cycle,
        guild.id,
        member.id,
        target_month=target_month,
        text_activity=round(text_xp, 1),
        vc_activity=round(vc_xp, 1),
        score=round(text_xp + vc_xp, 1),
        confirm_deadline=deadline.isoformat(),
        now_iso=now.isoformat(),
    )

    review = (
        await asyncio.to_thread(_member_store.get_item, guild.id, member.id)
    ).get("current_review") or {}

    if review.get("target_month") != target_month:
        # 別の月のサイクルが進行中（通常は起きない）
        return False

    deadline = _parse_iso(review.get("confirm_deadline")) or deadline

    review_role = guild.get_role(config.review_role_id)
    if review_role is not None and review_role not in member.roles:
        await member.add_roles(
            review_role,
            reason=f"rainbowl: {month_label(target_month)}分の活動確認対象",
        )

    if not review.get("review_channel_id"):
        channel = await _create_review_channel(
            guild, member, config, staff_role_id
        )
        await channel.send(
            rainbowl_texts.ACTIVITY_REVIEW_NOTICE_TEXT_TEMPLATE.format(
                mention=member.mention,
                month_label=month_label(target_month),
                deadline_label=deadline_label(deadline),
            )
        )
        await asyncio.to_thread(
            _member_store.set_review_fields,
            guild.id,
            member.id,
            target_month,
            {"review_channel_id": str(channel.id)},
            datetime.now(JST).isoformat(),
        )

    if not review.get("staff_message_id"):
        staff_channel = guild.get_channel(config.staff_list_channel_id)
        if isinstance(staff_channel, discord.TextChannel):
            message = await staff_channel.send(
                embed=_build_staff_embed(
                    member,
                    config,
                    target_month=target_month,
                    text_xp=review.get("text_activity", text_xp),
                    vc_xp=review.get("vc_activity", vc_xp),
                    prev_score=prev_score,
                    joined=joined,
                    deadline=deadline,
                ),
                view=_retain_view(member.id, target_month),
            )
            await asyncio.to_thread(
                _member_store.set_review_fields,
                guild.id,
                member.id,
                target_month,
                {"staff_message_id": str(message.id)},
                datetime.now(JST).isoformat(),
            )

    return True


async def _create_review_channel(
    guild: discord.Guild,
    member: discord.Member,
    config: ActivityReviewConfig,
    staff_role_id: int,
) -> discord.TextChannel:
    """個別連絡カテゴリー配下に、本人＋運営だけが見える在籍確認チャンネルを作る。"""
    category = guild.get_channel(config.review_category_id)
    staff_role = guild.get_role(staff_role_id)

    allow = discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
    )
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: allow,
    }
    if staff_role is not None:
        overwrites[staff_role] = allow

    name = normalize_text_channel_name(member.display_name) or "メンバー"

    return await guild.create_text_channel(
        name=f"在籍確認-{name}",
        category=(
            category if isinstance(category, discord.CategoryChannel) else None
        ),
        overwrites=overwrites,
        reason="rainbowl: 月次活動確認の本人専用チャンネル生成",
    )


def _build_staff_embed(
    member: discord.abc.User,
    config: ActivityReviewConfig,
    *,
    target_month: str,
    text_xp: float,
    vc_xp: float,
    prev_score: float,
    joined: Optional[datetime],
    deadline: datetime,
) -> discord.Embed:
    embed = discord.Embed(
        title="活動確認対象者",
        description=f"{member.mention}（`{member.id}`）",
        color=discord.Color.gold(),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="対象月", value=month_label(target_month))
    embed.add_field(
        name="活動スコア",
        value=f"{text_xp + vc_xp:.1f} / {config.min_score:g}",
    )
    embed.add_field(name="テキスト", value=f"{text_xp:.1f}")
    embed.add_field(name="VC", value=f"{vc_xp:.1f}")
    embed.add_field(name="前月活動量", value=f"{prev_score:.1f}")
    embed.add_field(
        name="入会日（新人）",
        value=f"{joined:%Y-%m-%d}" if joined else "不明",
    )
    embed.add_field(
        name="確認期限",
        value=deadline_label(deadline),
        inline=False,
    )
    return embed


async def _post_staff_summary(
    guild: discord.Guild,
    config: ActivityReviewConfig,
    text: str,
) -> None:
    channel = guild.get_channel(config.staff_list_channel_id)
    if not isinstance(channel, discord.TextChannel):
        print(
            "[activity_review] 対象者一覧チャンネルを取得できません"
            f" guild_id={guild.id}"
        )
        return
    try:
        await channel.send(text)
    except discord.HTTPException as exc:
        print(
            "[activity_review] 運営チャンネルへの投稿に失敗しました"
            f" guild_id={guild.id} error={exc}"
        )


# ========================================
# 2. 「残留」ボタン
# ========================================

async def process_retain_button(
    interaction: discord.Interaction,
    user_id: int,
    target_month: str,
) -> None:
    guild = interaction.guild
    staff = interaction.user

    if guild is None or not isinstance(staff, discord.Member):
        await interaction.response.send_message(
            "サーバー内で使用してください。", ephemeral=True
        )
        return

    try:
        rb_config = await get_rainbowl_config(guild.id)
    except RainbowlConfigError:
        rb_config = None
    config = await get_activity_review_config(guild.id)

    if rb_config is None or config is None:
        await interaction.response.send_message(
            "設定を取得できませんでした。", ephemeral=True
        )
        return

    if not is_admin_like(staff, rb_config):
        await interaction.response.send_message(
            "このボタンは運営専用です。", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    now = datetime.now(JST)
    retained = await asyncio.to_thread(
        _member_store.mark_review_retained,
        guild.id,
        user_id,
        target_month,
        staff.id,
        now.isoformat(),
    )

    if not retained:
        await interaction.followup.send(
            "既に処理済みか、確認期限を過ぎているため残留にできませんでした。",
            ephemeral=True,
        )
        return

    review = (
        await asyncio.to_thread(_member_store.get_item, guild.id, user_id)
    ).get("current_review") or {}
    deadline = _parse_iso(review.get("confirm_deadline"))

    member = guild.get_member(user_id)
    review_role = guild.get_role(config.review_role_id)
    if member is not None and review_role in member.roles:
        try:
            await member.remove_roles(
                review_role, reason="rainbowl: 活動確認 残留確定"
            )
        except discord.HTTPException as exc:
            print(
                "[activity_review] 確認用ロールの解除に失敗しました"
                f" guild_id={guild.id} user_id={user_id} error={exc}"
            )

    channel_id = review.get("review_channel_id")
    channel = guild.get_channel(int(channel_id)) if channel_id else None
    if isinstance(channel, discord.TextChannel) and deadline is not None:
        try:
            await channel.send(
                rainbowl_texts.ACTIVITY_REVIEW_RETAINED_TEXT_TEMPLATE.format(
                    deadline_label=deadline_label(deadline),
                )
            )
        except discord.HTTPException as exc:
            print(
                "[activity_review] 残留通知の投稿に失敗しました"
                f" guild_id={guild.id} user_id={user_id} error={exc}"
            )

    if interaction.message is not None:
        await _mark_staff_message(
            interaction.message,
            f"✅ 残留確定（{staff.display_name}）",
            discord.Color.green(),
        )

    await interaction.followup.send(
        f"<@{user_id}> を残留にしました。", ephemeral=True
    )


async def _mark_staff_message(
    message: discord.Message,
    result: str,
    color: discord.Color,
) -> None:
    """運営チャンネルの対象者メッセージに結果を追記し、ボタンを無効化する。"""
    embed = message.embeds[0] if message.embeds else discord.Embed()
    embed.color = color
    embed.add_field(name="結果", value=result, inline=False)
    try:
        await message.edit(embed=embed, view=_closed_view(result[:80]))
    except discord.HTTPException as exc:
        print(
            "[activity_review] 対象者メッセージの更新に失敗しました"
            f" message_id={message.id} error={exc}"
        )


# ========================================
# 3. 確認期限（毎日チェック）
# ========================================

async def process_review_deadlines(
    guild: discord.Guild,
    config: ActivityReviewConfig,
) -> Optional[str]:
    """
    確認期限を過ぎた在籍確認サイクルを締める。
    - RETAINED：アーカイブのみ
    - PENDING：キック有効の月ならキック、無効の月なら見送り（ロール解除）
    - 退出済み：アーカイブのみ
    いずれも本人専用チャンネルを削除し、運営チャンネルの対象者メッセージに結果を追記する。
    処理対象が無ければNone。
    """
    now = datetime.now(JST)
    items = await asyncio.to_thread(
        _member_store.list_reviews_in_progress, guild.id
    )

    counts = {"KICKED": 0, "RETAINED": 0, "CANCELLED": 0, "LEFT": 0}
    months = set()

    for item in items:
        review = item.get("current_review") or {}
        deadline = _parse_iso(review.get("confirm_deadline"))

        if deadline is None or deadline > now:
            continue

        try:
            decision = await _close_review(
                guild, config, int(item["user_id"]), review
            )
        except Exception as exc:
            print(
                "[activity_review] 確認期限の処理に失敗しました:"
                f" guild_id={guild.id} user_id={item.get('user_id')}"
                f" error={exc}"
            )
            continue

        if decision:
            counts[decision] += 1
            months.add(review.get("target_month"))

    if not months:
        return None

    summary = (
        f"🗂 {'・'.join(month_label(m) for m in sorted(months))}分の活動確認を締めました："
        f"キック {counts['KICKED']}名／残留 {counts['RETAINED']}名"
        f"／見送り（キック無効）{counts['CANCELLED']}名"
        f"／退出済み {counts['LEFT']}名"
    )
    await _post_staff_summary(guild, config, summary)
    return summary


async def _close_review(
    guild: discord.Guild,
    config: ActivityReviewConfig,
    user_id: int,
    review: Dict[str, Any],
) -> Optional[str]:
    target_month = review["target_month"]
    status = review.get("status")
    member = guild.get_member(user_id)
    decided_by: Optional[int] = None
    reason: Optional[str] = None

    if status == "RETAINED":
        decision = "RETAINED"
        decided_by = int(review["retained_by"]) if review.get("retained_by") else None

    elif member is None:
        decision = "LEFT"
        reason = "確認期限時点で退出済み"

    elif status == "PENDING" and not await is_kick_enabled(
        guild.id, target_month
    ):
        decision = "CANCELLED"
        reason = "キック無効の月のため見送り"
        review_role = guild.get_role(config.review_role_id)
        if review_role in member.roles:
            await member.remove_roles(
                review_role, reason="rainbowl: 活動確認 キック見送り"
            )

    else:
        # PENDING → KICKINGへ条件付きで進めてからキック（残留ボタンとの競合防止）。
        # 前回キック途中で落ちたKICKINGはそのまま再実行する。
        if status == "PENDING":
            claimed = await asyncio.to_thread(
                _member_store.claim_review_kick,
                guild.id,
                user_id,
                target_month,
            )
            if not claimed:
                return None
        elif status != "KICKING":
            return None

        decision = "KICKED"
        reason = "活動量基準未達・確認期限までに残留確認なし"
        await member.kick(
            reason=(
                f"rainbowl: {month_label(target_month)}分の活動確認"
                "（期限までに残留確認なし）"
            )
        )

    resolved = await asyncio.to_thread(
        _member_store.resolve_review_cycle,
        guild.id,
        user_id,
        decision=decision,
        decided_by=decided_by,
        reason=reason,
        now_iso=datetime.now(JST).isoformat(),
    )
    if not resolved:
        return None

    channel_id = review.get("review_channel_id")
    channel = guild.get_channel(int(channel_id)) if channel_id else None
    if channel is not None:
        try:
            await channel.delete(reason="rainbowl: 活動確認の確認期限到来")
        except discord.HTTPException as exc:
            print(
                "[activity_review] 在籍確認チャンネルの削除に失敗しました"
                f" guild_id={guild.id} user_id={user_id} error={exc}"
            )

    if decision != "RETAINED":
        await _update_staff_message_result(guild, config, review, decision)

    return decision


async def _update_staff_message_result(
    guild: discord.Guild,
    config: ActivityReviewConfig,
    review: Dict[str, Any],
    decision: str,
) -> None:
    message_id = review.get("staff_message_id")
    channel = guild.get_channel(config.staff_list_channel_id)
    if not message_id or not isinstance(channel, discord.TextChannel):
        return

    try:
        message = await channel.fetch_message(int(message_id))
    except discord.HTTPException:
        return

    result, color = {
        "KICKED": ("🚪 キック済み", discord.Color.red()),
        "CANCELLED": ("⏸ 見送り（キック無効の月）", discord.Color.light_grey()),
        "LEFT": ("👋 期限前に退出済み", discord.Color.light_grey()),
    }[decision]
    await _mark_staff_message(message, result, color)
