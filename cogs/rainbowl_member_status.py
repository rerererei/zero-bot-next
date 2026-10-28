# cogs/rainbowl_member_status.py
"""
rainbowl：メンバー1人分の状態を、各DBからまとめて一目で確認する管理者専用コマンド。

/member_status target:(メンバー)
  - Discord：在籍・参加日・アカウント作成日・管理者扱いか・主なロール
  - 入会フロー（zero_bot_rainbowl_applicants）：ステータス・入場回数・申請日・新人になった日・合否理由・過去の挑戦
  - 在籍確認（zero_bot_rainbowl_member_state）：個別除外・進行中の在籍確認・過去の結果
  - 活動量：今月（実行時点まで）と前月のスコア（月次活動確認と同じ計算）
  - XP：VC・テキストのXPとレベル
  - 所有中の部屋（プライベートルーム機能）

読み取り専用（DB・ロールには一切書き込まない）。結果は実行したチャンネルに通常投稿する
（管理者同士で共有して確認するため。管理者用チャンネルで使うこと）。
退出済みの人も、ユーザーIDを指定すれば確認できる。
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from data.rainbowl.applicants_store import RainbowlStore
from data.rainbowl.member_state_store import MemberStateStore
from data.xp_router import calc_level_from_xp, get_text_xp, get_voice_xp
from services import private_room_service
from services.rainbowl_activity_review_service import (
    JST,
    _collect_scores,
    _newcomer_at,
    get_activity_review_config,
    month_label,
    month_of,
    previous_month,
)
from services.rainbowl_config_service import (
    RainbowlConfigError,
    get_rainbowl_config,
    is_admin_like,
)
from services.rainbowl_onboarding_service import STATUS_LABELS


_applicants_store = RainbowlStore()
_member_store = MemberStateStore()

REVIEW_STATUS_LABELS = {
    "PENDING": "確認待ち（残留未確定）",
    "RETAINED": "残留確定",
    "KICKING": "キック処理中",
}

REVIEW_DECISION_LABELS = {
    "RETAINED": "残留",
    "KICKED": "キック",
    "CANCELLED": "見送り（キック無効の月）",
    "LEFT": "退出済み",
}

FIELD_MAX = 1024


def _ts(value: Optional[str], style: str = "f") -> str:
    """ISO8601文字列をDiscordのタイムスタンプ表記にする（無ければ「—」）。"""
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return value
    return f"<t:{int(dt.timestamp())}:{style}>"


def _dt(value: Optional[datetime], style: str = "f") -> str:
    if value is None:
        return "—"
    return f"<t:{int(value.timestamp())}:{style}>"


def _clip(text: str) -> str:
    return text if len(text) <= FIELD_MAX else text[: FIELD_MAX - 1] + "…"


def _discord_section(
    guild: discord.Guild,
    user: discord.abc.User,
    member: Optional[discord.Member],
    rb_config,
) -> str:
    lines = [
        f"{user.mention}（`{user.name}` / `{user.id}`）",
        f"アカウント作成：{_dt(user.created_at, 'D')}（{_dt(user.created_at, 'R')}）",
    ]

    if member is None:
        lines.append("在籍：**退出済み**（サーバーにいません）")
        return "\n".join(lines)

    lines.append(f"在籍：在籍中／参加 {_dt(member.joined_at, 'D')}（{_dt(member.joined_at, 'R')}）")

    if rb_config is not None and is_admin_like(member, rb_config):
        lines.append("管理者扱い：**はい**（自動キック・月次活動確認の対象外）")

    roles = [
        role.mention
        for role in sorted(member.roles, key=lambda r: r.position, reverse=True)
        if role != guild.default_role
    ]
    lines.append("ロール：" + (" ".join(roles) if roles else "なし"))
    return "\n".join(lines)


def _applicant_section(applicant: Dict[str, Any]) -> str:
    if not applicant:
        return "記録なし（rainbowl導入前からいる人、または一度も入場していない）"

    status = applicant.get("status")
    lines = [
        f"ステータス：**{STATUS_LABELS.get(status, status or '—')}**",
        f"入場回数：{applicant.get('join_count', '—')}回"
        f"（初回 {_ts(applicant.get('first_joined_at'), 'D')}／直近 {_ts(applicant.get('last_joined_at'), 'D')}）",
        f"入会申請：{_ts(applicant.get('applied_at'))}",
    ]

    newcomer = _newcomer_at(applicant)
    if applicant.get("newcomer_at"):
        lines.append(f"新人になった日：{_dt(newcomer, 'D')}")
    elif newcomer is not None:
        lines.append(
            f"新人になった日：{_dt(newcomer, 'D')}"
            "（記録が無いため最終更新日時で代用。月次活動確認の判定も同じ）"
        )
    else:
        lines.append("新人になった日：—")

    if applicant.get("verdict_reason"):
        lines.append(f"合否・キック理由：{applicant['verdict_reason']}")

    history = applicant.get("application_history") or []
    if history:
        last = history[-1]
        last_status = last.get("final_status")
        lines.append(
            f"過去の挑戦：{len(history)}件"
            f"（直近：{STATUS_LABELS.get(last_status, last_status or '—')}"
            + (f"／{last['verdict_reason']}" if last.get("verdict_reason") else "")
            + "）"
        )

    return "\n".join(lines)


def _member_state_section(state: Dict[str, Any]) -> str:
    if not state:
        return "記録なし（個別除外・在籍確認の対象になったことがない）"

    lines: List[str] = []

    exempt = state.get("exempt") or {}
    if exempt.get("is_exempt"):
        lines.append(
            f"個別除外：**登録中**（{exempt.get('reason') or '理由なし'}"
            f"／{_ts(exempt.get('granted_at'), 'D')} <@{exempt.get('granted_by')}>）"
        )
    else:
        lines.append("個別除外：なし")

    pause = state.get("pause") or {}
    if pause.get("is_paused"):
        lines.append(f"休止（旧データ）：登録中（{pause.get('reason') or '理由なし'}）")

    review = state.get("current_review")
    if review:
        status = review.get("status")
        lines.append(
            f"進行中の在籍確認：**{month_label(review['target_month'])}分**"
            f"／{REVIEW_STATUS_LABELS.get(status, status)}"
            f"／期限 {_ts(review.get('confirm_deadline'))}"
            f"／スコア {float(review.get('score', 0)):.1f}"
        )
    else:
        lines.append("進行中の在籍確認：なし")

    history = state.get("review_history") or []
    if history:
        recent = "、".join(
            f"{month_label(h['target_month'])} "
            f"{REVIEW_DECISION_LABELS.get(h.get('decision'), h.get('decision'))}"
            for h in history[-3:]
            if h.get("target_month")
        )
        lines.append(f"過去の在籍確認：{len(history)}件（直近：{recent}）")

    return "\n".join(lines)


def _activity_section(
    user_id: int,
    this_month: str,
    this_scores: Dict[int, Any],
    prev_scores: Dict[int, Any],
    min_score: Optional[float],
) -> str:
    lines = []
    for month, scores, suffix in (
        (this_month, this_scores, "（実行時点まで）"),
        (previous_month(this_month), prev_scores, ""),
    ):
        text_xp, vc_xp = scores.get(user_id, (0.0, 0.0))
        score = text_xp + vc_xp
        mark = ""
        if min_score is not None:
            mark = " ✅" if score >= min_score else f" ⚠️基準{min_score:g}未満"
        lines.append(
            f"{month_label(month)}{suffix}：**{score:.1f}**{mark}"
            f"（VC {vc_xp:.1f}／テキスト {text_xp:.1f}）"
        )
    return "\n".join(lines)


def _xp_section(guild_id: int, user_id: int) -> str:
    voice_xp = get_voice_xp(guild_id, user_id)
    text_xp = get_text_xp(guild_id, user_id)
    v_lv, _, _ = calc_level_from_xp(guild_id, voice_xp)
    t_lv, _, _ = calc_level_from_xp(guild_id, text_xp)
    return (
        f"VC：Lv.{v_lv}（{voice_xp:.1f} XP）\n"
        f"テキスト：Lv.{t_lv}（{text_xp:.1f} XP）"
    )


def _room_section(room: Optional[Dict[str, Any]]) -> str:
    if not room:
        return "なし"
    room_type = room.get("room_type")
    return (
        f"<#{room['channel_id']}>（"
        f"{private_room_service.ROOM_TYPE_DISPLAY_NAMES.get(room_type, room_type)}"
        f"／{room.get('state', '—')}）"
    )


class RainbowlMemberStatus(commands.Cog):
    """rainbowl：メンバー1人分の状態確認（管理者専用・読み取りのみ）"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="member_status",
        description="メンバー1人分の状態（入会フロー・在籍確認・活動量・XP・部屋）をまとめて確認します（管理者専用）",
    )
    @app_commands.describe(
        target="確認するメンバー（退出済みの人はユーザーIDで指定）",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def member_status(
        self,
        interaction: discord.Interaction,
        target: discord.User,
    ) -> None:
        await interaction.response.defer(thinking=True)

        guild = interaction.guild
        member = guild.get_member(target.id)

        try:
            rb_config = await get_rainbowl_config(guild.id)
        except RainbowlConfigError:
            rb_config = None

        this_month = month_of(datetime.now(JST).date())

        (
            applicant,
            state,
            this_scores,
            prev_scores,
            review_config,
            xp_text,
            room,
        ) = await asyncio.gather(
            asyncio.to_thread(_applicants_store.get_item, guild.id, target.id),
            asyncio.to_thread(_member_store.get_item, guild.id, target.id),
            asyncio.to_thread(_collect_scores, guild.id, this_month),
            asyncio.to_thread(
                _collect_scores, guild.id, previous_month(this_month)
            ),
            get_activity_review_config(guild.id),
            asyncio.to_thread(_xp_section, guild.id, target.id),
            asyncio.to_thread(
                private_room_service.store.get_room_by_owner,
                guild.id,
                target.id,
            ),
        )

        embed = discord.Embed(
            title=f"メンバー状態：{target.display_name}",
            color=discord.Color.teal(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_thumbnail(url=target.display_avatar.url)

        sections = [
            ("👤 Discord", _discord_section(guild, target, member, rb_config)),
            ("🚪 入会フロー", _applicant_section(applicant)),
            ("📋 在籍確認", _member_state_section(state)),
            (
                "📊 活動量（月次活動確認のスコア）",
                _activity_section(
                    target.id,
                    this_month,
                    this_scores,
                    prev_scores,
                    review_config.min_score if review_config else None,
                ),
            ),
            ("⭐ XP", xp_text),
            ("🎙️ 所有中の部屋", _room_section(room)),
        ]
        for name, value in sections:
            embed.add_field(name=name, value=_clip(value), inline=False)

        await interaction.followup.send(embed=embed)

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        original = getattr(error, "original", error)
        print(
            "[member_status] コマンドエラー"
            f" guild_id={interaction.guild_id} error={original!r}"
        )
        message = "状態の取得に失敗しました。"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RainbowlMemberStatus(bot))
