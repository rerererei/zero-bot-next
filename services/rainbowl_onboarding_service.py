# services/rainbowl_onboarding_service.py
"""
rainbowl機能（入場〜面談合否判定フロー）の状態遷移ロジック本体。

Cog（cogs/rainbowl_onboarding.py, cogs/rainbowl_interview.py）は
Discordイベント・インタラクションの受け口として薄く保ち、
判定・DynamoDB操作・チャンネル操作はこのモジュールに集約する。
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import discord

from data.rainbowl_store import RainbowlStore
from services import rainbowl_texts
from services.rainbowl_config_service import RainbowlGuildConfig
from utils.helpers import normalize_text_channel_name


JST = timezone(timedelta(hours=9))

STATUS_LABELS = {
    "NOT_APPLIED": "入場のみ・未申請",
    "APPLIED": "申請済み・プロフィール未提出",
    "PROFILE_SUBMITTED": "プロフィール提出済み・承認待ち",
    "SCHEDULING": "日程調整中",
    "INTERVIEW_DONE": "面談実施済み・判定待ち",
    "PASSED": "合格",
    "REJECTED": "不合格",
    "WITHDRAWN": "辞退",
}

store = RainbowlStore()


def _now_iso() -> str:
    return datetime.now(JST).isoformat()


def _format_account_age(created_at: datetime) -> str:
    """アカウント作成日から「n年nヶ月」形式の経過期間を作る。"""
    now = datetime.now(timezone.utc)
    delta_days = (now - created_at).days

    years = delta_days // 365
    months = (delta_days % 365) // 30

    if years and months:
        return f"{years}年{months}ヶ月"

    if years:
        return f"{years}年"

    if months:
        return f"{months}ヶ月"

    return f"{delta_days}日"


# ============================================================
#  入会案内カテゴリーの初期権限（べき等セットアップ）
# ============================================================

async def ensure_onboarding_entry_permissions(
    guild: discord.Guild,
    config: RainbowlGuildConfig,
) -> None:
    """
    入会案内カテゴリーの初期権限を確認し、必要な分だけ設定する。

    - 入場者ロールに対し、カテゴリー自体はview_channel:deny
    - 入場者ロールに対し、onboarding_channel_ids[0]（ようこそ）だけview_channel:allow

    既に正しい状態ならDiscord APIを呼ばない。
    """
    entrant_role = guild.get_role(config.entrant_role_id)

    if entrant_role is None:
        print(
            "[rainbowl] entrant_role が見つかりません"
            f" guild_id={guild.id}"
        )
        return

    category = guild.get_channel(config.onboarding_category_id)

    if isinstance(category, discord.CategoryChannel):
        category_overwrite = category.overwrites_for(entrant_role)

        if category_overwrite.view_channel is not False:
            category_overwrite.view_channel = False

            try:
                await category.set_permissions(
                    entrant_role,
                    overwrite=category_overwrite,
                    reason="rainbowl: 入会案内カテゴリー初期権限（deny）",
                )
            except discord.HTTPException as exc:
                print(
                    "[rainbowl] カテゴリー権限の設定に失敗しました"
                    f" guild_id={guild.id} error={exc}"
                )

    if not config.onboarding_channel_ids:
        return

    welcome_channel = guild.get_channel(
        config.onboarding_channel_ids[0]
    )

    if welcome_channel is None:
        return

    welcome_overwrite = welcome_channel.overwrites_for(
        entrant_role
    )

    if welcome_overwrite.view_channel is not True:
        welcome_overwrite.view_channel = True

        try:
            await welcome_channel.set_permissions(
                entrant_role,
                overwrite=welcome_overwrite,
                reason="rainbowl: ようこそチャンネル初期権限（allow）",
            )
        except discord.HTTPException as exc:
            print(
                "[rainbowl] ようこそチャンネル権限の設定に失敗しました"
                f" guild_id={guild.id} error={exc}"
            )


# ============================================================
#  入場処理
# ============================================================

async def process_member_join(
    member: discord.Member,
    config: RainbowlGuildConfig,
) -> Dict[str, Any]:
    """
    on_member_joinの主処理。

    前回挑戦の履歴アーカイブ・入場回数の更新・権限のべき等セットアップ・
    入場者ロールの付与を行い、更新後のDBアイテムを返す
    （入場ログEmbedの生成に使う）。
    """
    now_iso = _now_iso()

    item = await asyncio.to_thread(
        store.record_join,
        member.guild.id,
        member.id,
        now_iso,
    )

    await ensure_onboarding_entry_permissions(
        member.guild,
        config,
    )

    entrant_role = member.guild.get_role(
        config.entrant_role_id
    )

    if entrant_role is not None:
        try:
            await member.add_roles(
                entrant_role,
                reason="rainbowl: 入場",
            )
        except discord.HTTPException as exc:
            print(
                "[rainbowl] entrant_role付与に失敗しました"
                f" guild_id={member.guild.id}"
                f" user_id={member.id} error={exc}"
            )

    return item


async def fetch_user_bio(
    bot: discord.Client,
    user_id: int,
) -> Optional[str]:
    """
    Discordのユーザー自己紹介文（bio）を取得する。

    Botトークンでこのエンドポイントを利用できるかは未検証。
    失敗した場合は例外を投げず、常にNoneを返す
    （呼び出し側は「取得不可」として表示すること）。
    """
    try:
        route = discord.http.Route(
            "GET",
            "/users/{user_id}/profile",
            user_id=user_id,
        )

        data = await bot.http.request(route)

    except Exception as exc:
        print(
            "[rainbowl] bio取得に失敗しました"
            "（未対応の可能性があります）"
            f" user_id={user_id} error={exc}"
        )
        return None

    if not isinstance(data, dict):
        return None

    user_data = data.get("user")

    if not isinstance(user_data, dict):
        return None

    bio = user_data.get("bio")

    if isinstance(bio, str) and bio.strip():
        return bio.strip()

    return None


def build_join_log_embed(
    member: discord.Member,
    item: Dict[str, Any],
    bio: Optional[str],
) -> discord.Embed:
    """入場ログEmbed（join_log_channel_id用）を組み立てる。"""
    join_count = item.get("join_count", 1)
    latest_history = store.get_latest_history_entry(item)

    embed = discord.Embed(
        title="🚪 入場者詳細",
        color=discord.Color.blue(),
        timestamp=discord.utils.utcnow(),
    )

    embed.set_thumbnail(url=member.display_avatar.url)

    embed.add_field(
        name="表示名",
        value=member.display_name,
        inline=True,
    )

    embed.add_field(
        name="ユーザー名",
        value=f"@{member.name}",
        inline=True,
    )

    embed.add_field(
        name="ユーザーID",
        value=f"`{member.id}`",
        inline=True,
    )

    created_at = member.created_at
    account_age_text = _format_account_age(created_at)
    unix_timestamp = int(created_at.timestamp())

    embed.add_field(
        name="アカウント作成日",
        value=(
            f"<t:{unix_timestamp}:D>"
            f"（{account_age_text}）"
        ),
        inline=True,
    )

    embed.add_field(
        name="入場回数",
        value=f"{join_count}回目",
        inline=True,
    )

    embed.add_field(
        name="自己紹介文（bio）",
        value=bio if bio else "取得不可",
        inline=False,
    )

    if latest_history is None:
        embed.add_field(
            name="前回の審査状況",
            value="初回入場",
            inline=False,
        )
    else:
        final_status = latest_history.get(
            "final_status",
            "NOT_APPLIED",
        )

        status_label = STATUS_LABELS.get(
            final_status,
            final_status,
        )

        embed.add_field(
            name="前回どこまで進んだか",
            value=status_label,
            inline=False,
        )

        if final_status in ("PASSED", "REJECTED"):
            verdict_reason = (
                latest_history.get("verdict_reason")
                or "（記録なし）"
            )

            verdict_label = (
                "合格" if final_status == "PASSED"
                else "不合格"
            )

            embed.add_field(
                name="前回の合否・理由",
                value=f"{verdict_label}\n{verdict_reason}",
                inline=False,
            )

    return embed


# ============================================================
#  入会案内カテゴリーの段階開放
# ============================================================

async def process_next_button(
    member: discord.Member,
    config: RainbowlGuildConfig,
    button_step: int,
) -> Optional[int]:
    """
    「次へ」ボタン押下時の処理。

    button_stepは押されたボタンが示すステップ番号。
    現在のonboarding_stepと一致する場合のみ、次のチャンネルを
    本人だけに開放する。

    成功した場合は開放したstep番号を、
    不一致・範囲外・競合の場合はNoneを返す。
    """
    guild = member.guild

    item = await asyncio.to_thread(
        store.get_item,
        guild.id,
        member.id,
    )

    current_step = item.get("onboarding_step", 1)

    if current_step != button_step:
        return None

    next_step = button_step + 1

    if next_step > len(config.onboarding_channel_ids):
        return None

    now_iso = _now_iso()

    success = await asyncio.to_thread(
        store.advance_onboarding_step,
        guild.id,
        member.id,
        current_step,
        next_step,
        now_iso,
    )

    if not success:
        return None

    next_channel = guild.get_channel(
        config.onboarding_channel_ids[next_step - 1]
    )

    if next_channel is not None:
        try:
            await next_channel.set_permissions(
                member,
                view_channel=True,
                reason="rainbowl: 入会案内の段階開放",
            )
        except discord.HTTPException as exc:
            print(
                "[rainbowl] 段階開放の権限設定に失敗しました"
                f" guild_id={guild.id} user_id={member.id}"
                f" step={next_step} error={exc}"
            )

    return next_step


async def cleanup_onboarding_overrides(
    guild: discord.Guild,
    member: discord.Member,
    config: RainbowlGuildConfig,
) -> None:
    """
    申請中ロール付与時に、チャンネル2〜7に残っている
    本人向け個別オーバーライドを削除する。
    """
    for channel_id in config.onboarding_channel_ids[1:]:
        channel = guild.get_channel(channel_id)

        if channel is None:
            continue

        if member not in channel.overwrites:
            continue

        try:
            await channel.set_permissions(
                member,
                overwrite=None,
                reason=(
                    "rainbowl: 申請中ロール付与による"
                    "個別オーバーライドの後片付け"
                ),
            )
        except discord.HTTPException as exc:
            print(
                "[rainbowl] 個別オーバーライドの削除に失敗しました"
                f" guild_id={guild.id} user_id={member.id}"
                f" channel_id={channel_id} error={exc}"
            )


# ============================================================
#  入会申請
# ============================================================

def build_applicant_channel_name(
    member: discord.Member,
) -> str:
    """
    本人専用チャンネル名を組み立てる。

    Discordのチャンネル名制約（半角スペース→ハイフン変換、
    絵文字等の除去）による見え方は未検証（要実装後確認）。
    """
    normalized_display_name = normalize_text_channel_name(
        member.display_name
    )

    if not normalized_display_name:
        normalized_display_name = "応募者"

    return f"{normalized_display_name}さん（{member.id}）"


async def create_applicant_channel(
    guild: discord.Guild,
    member: discord.Member,
    config: RainbowlGuildConfig,
) -> discord.TextChannel:
    """
    面談・手続きカテゴリー配下に、本人専用チャンネルを生成する。

    閲覧権限は本人＋staff_role_idのみ。
    """
    category = guild.get_channel(
        config.interview_category_id
    )

    staff_role = guild.get_role(config.staff_role_id)

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=False,
        ),
        member: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
        ),
    }

    if staff_role is not None:
        overwrites[staff_role] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
        )

    channel = await guild.create_text_channel(
        name=build_applicant_channel_name(member),
        category=(
            category
            if isinstance(category, discord.CategoryChannel)
            else None
        ),
        overwrites=overwrites,
        reason="rainbowl: 入会申請による本人専用チャンネル生成",
    )

    return channel


async def send_applicant_channel_intro(
    channel: discord.TextChannel,
) -> None:
    """本人専用チャンネル生成直後の案内文・テンプレートを2通投稿する。"""
    await channel.send(
        rainbowl_texts.APPLICANT_CHANNEL_GUIDE_TEXT
    )

    await channel.send(
        rainbowl_texts.PROFILE_TEMPLATE_TEXT
    )


async def process_apply_button(
    member: discord.Member,
    config: RainbowlGuildConfig,
) -> Optional[discord.TextChannel]:
    """
    「入会申請」ボタン押下時の処理。

    statusがNOT_APPLIED（または未登録）の場合のみ処理を進める。
    既に申請済みの場合はNoneを返す。
    """
    guild = member.guild

    item = await asyncio.to_thread(
        store.get_item,
        guild.id,
        member.id,
    )

    current_status = item.get("status") if item else None

    if current_status not in (None, "NOT_APPLIED"):
        return None

    channel = await create_applicant_channel(
        guild,
        member,
        config,
    )

    now_iso = _now_iso()

    success = await asyncio.to_thread(
        store.set_applied,
        guild.id,
        member.id,
        channel.id,
        now_iso,
    )

    if not success:
        # 直前のチェックと実際の更新の間に競合が発生した場合の
        # ロールバック（二重申請対策）
        try:
            await channel.delete(
                reason="rainbowl: 二重申請のためロールバック"
            )
        except discord.HTTPException:
            pass

        return None

    applicant_role = guild.get_role(
        config.applicant_role_id
    )

    entrant_role = guild.get_role(config.entrant_role_id)

    if applicant_role is not None:
        try:
            await member.add_roles(
                applicant_role,
                reason="rainbowl: 入会申請",
            )
        except discord.HTTPException as exc:
            print(
                "[rainbowl] applicant_role付与に失敗しました"
                f" guild_id={guild.id} user_id={member.id}"
                f" error={exc}"
            )

    if (
        entrant_role is not None
        and entrant_role in member.roles
    ):
        try:
            await member.remove_roles(
                entrant_role,
                reason="rainbowl: 入会申請",
            )
        except discord.HTTPException as exc:
            print(
                "[rainbowl] entrant_role削除に失敗しました"
                f" guild_id={guild.id} user_id={member.id}"
                f" error={exc}"
            )

    await send_applicant_channel_intro(channel)

    await cleanup_onboarding_overrides(
        guild,
        member,
        config,
    )

    return channel


# ============================================================
#  面接用プロフィール検知・承認転記
# ============================================================

async def process_profile_candidate_message(
    message: discord.Message,
    config: RainbowlGuildConfig,
) -> bool:
    """
    on_messageから呼ばれる。

    本人専用チャンネルへの本人による最初の投稿を面接用プロフィールとして
    扱い、「受付」スタンプを設置する。処理した場合はTrueを返す。
    """
    item = await asyncio.to_thread(
        store.get_item,
        message.guild.id,
        message.author.id,
    )

    applicant_channel_id = item.get("applicant_channel_id")

    if (
        applicant_channel_id is None
        or int(applicant_channel_id) != message.channel.id
    ):
        return False

    now_iso = _now_iso()

    success = await asyncio.to_thread(
        store.set_profile_submitted,
        message.guild.id,
        message.author.id,
        message.id,
        now_iso,
    )

    if not success:
        return False

    emoji = discord.PartialEmoji(
        name=config.reception_emoji_name,
        id=config.reception_emoji_id,
    )

    try:
        await message.add_reaction(emoji)
    except discord.HTTPException as exc:
        print(
            "[rainbowl] 受付スタンプの設置に失敗しました"
            f" guild_id={message.guild.id}"
            f" user_id={message.author.id} error={exc}"
        )

    return True


async def process_reception_reaction(
    payload: discord.RawReactionActionEvent,
    bot: discord.Client,
    config: RainbowlGuildConfig,
) -> None:
    """
    on_raw_reaction_addから呼ばれる。

    運営が面接用プロフィールへ「受付」スタンプを追加した場合に、
    応募者プロフ置き場チャンネルへ転記する。
    """
    if (
        payload.emoji.id is None
        or payload.emoji.id != config.reception_emoji_id
    ):
        return

    guild = bot.get_guild(payload.guild_id)

    if guild is None:
        return

    reactor = guild.get_member(payload.user_id)

    if reactor is None or reactor.bot:
        return

    staff_role = guild.get_role(config.staff_role_id)

    if staff_role is None or staff_role not in reactor.roles:
        return

    channel = guild.get_channel(payload.channel_id)

    if channel is None:
        return

    try:
        message = await channel.fetch_message(
            payload.message_id
        )
    except discord.HTTPException:
        return

    applicant_id = message.author.id

    item = await asyncio.to_thread(
        store.get_item,
        guild.id,
        applicant_id,
    )

    profile_message_id = item.get("profile_message_id")

    if (
        profile_message_id is None
        or int(profile_message_id) != payload.message_id
    ):
        return

    now_iso = _now_iso()

    success = await asyncio.to_thread(
        store.set_scheduling,
        guild.id,
        applicant_id,
        now_iso,
    )

    if not success:
        return

    review_profiles_channel = guild.get_channel(
        config.review_profiles_channel_id
    )

    if review_profiles_channel is None:
        print(
            "[rainbowl] 応募者プロフ置き場チャンネルを"
            f"取得できません guild_id={guild.id}"
        )
        return

    applicant_member = guild.get_member(applicant_id)

    mention = (
        applicant_member.mention if applicant_member
        else f"<@{applicant_id}>"
    )

    try:
        await review_profiles_channel.send(
            f"{mention}\n{message.content}"
        )
    except discord.HTTPException as exc:
        print(
            "[rainbowl] プロフィールの転記に失敗しました"
            f" guild_id={guild.id} user_id={applicant_id}"
            f" error={exc}"
        )


# ============================================================
#  合否判定（/ok /ng）
# ============================================================

async def resolve_applicant_from_channel(
    channel: discord.TextChannel,
    config: RainbowlGuildConfig,
) -> Optional[discord.Member]:
    """
    本人専用チャンネルの権限オーバーライドから、対象の応募者を特定する。

    このチャンネルには staff_role・@everyone 以外に、
    ちょうど1人だけメンバー単位のオーバーライドが設定されている前提
    （create_applicant_channelでの生成方法に依存）。

    DB側のapplicant_channel_idとこのチャンネルが一致する場合のみ
    Memberを返す（実行チャンネルの検証を兼ねる）。
    """
    candidate: Optional[discord.Member] = None

    for target in channel.overwrites:
        if isinstance(target, discord.Member):
            candidate = target
            break

    if candidate is None:
        return None

    item = await asyncio.to_thread(
        store.get_item,
        channel.guild.id,
        candidate.id,
    )

    applicant_channel_id = item.get("applicant_channel_id")

    if (
        applicant_channel_id is None
        or int(applicant_channel_id) != channel.id
    ):
        return None

    return candidate

def build_verdict_embed(
    member: discord.Member,
    passed: bool,
    reason_or_comment: str,
) -> discord.Embed:
    """合否記録チャンネルへ投稿するEmbedを組み立てる。"""
    embed = discord.Embed(
        title="✅ 合格" if passed else "❌ 不合格",
        color=(
            discord.Color.green() if passed
            else discord.Color.red()
        ),
        timestamp=discord.utils.utcnow(),
    )

    embed.set_thumbnail(url=member.display_avatar.url)

    embed.add_field(
        name="ユーザー名",
        value=member.display_name,
        inline=True,
    )

    embed.add_field(
        name="ユーザーID",
        value=f"`{member.id}`",
        inline=True,
    )

    embed.add_field(
        name="コメント" if passed else "理由",
        value=reason_or_comment or "（記入なし）",
        inline=False,
    )

    return embed


async def process_pass_verdict(
    member: discord.Member,
    comment: str,
    config: RainbowlGuildConfig,
) -> None:
    """`/ok`モーダル送信時の確定処理。"""
    guild = member.guild
    now_iso = _now_iso()

    await asyncio.to_thread(
        store.set_passed,
        guild.id,
        member.id,
        comment,
        now_iso,
    )

    passed_role = guild.get_role(config.passed_role_id)
    applicant_role = guild.get_role(
        config.applicant_role_id
    )

    if passed_role is not None:
        try:
            await member.add_roles(
                passed_role,
                reason="rainbowl: 面談合格",
            )
        except discord.HTTPException as exc:
            print(
                "[rainbowl] passed_role付与に失敗しました"
                f" guild_id={guild.id} user_id={member.id}"
                f" error={exc}"
            )

    if (
        applicant_role is not None
        and applicant_role in member.roles
    ):
        try:
            await member.remove_roles(
                applicant_role,
                reason="rainbowl: 面談合格",
            )
        except discord.HTTPException as exc:
            print(
                "[rainbowl] applicant_role削除に失敗しました"
                f" guild_id={guild.id} user_id={member.id}"
                f" error={exc}"
            )

    passed_notice_channel = guild.get_channel(
        config.passed_notice_channel_id
    )

    if passed_notice_channel is not None:
        try:
            await passed_notice_channel.send(
                rainbowl_texts.PASSED_NOTICE_TEXT_TEMPLATE.format(
                    mention=member.mention,
                )
            )
        except discord.HTTPException as exc:
            print(
                "[rainbowl] 合格通知の投稿に失敗しました"
                f" guild_id={guild.id} user_id={member.id}"
                f" error={exc}"
            )

    await _delete_applicant_channel(
        guild,
        member.id,
        reason="rainbowl: 面談合格につき即時削除",
    )


async def process_reject_verdict(
    member: discord.Member,
    reason: str,
    config: RainbowlGuildConfig,
) -> bool:
    """
    `/ng`モーダル送信時の確定処理。

    直前に最新statusを再取得し、既にPASSEDであれば中断する。
    実行した場合はTrue、中断した場合はFalseを返す。
    """
    guild = member.guild
    now_iso = _now_iso()

    success = await asyncio.to_thread(
        store.set_rejected,
        guild.id,
        member.id,
        reason,
        now_iso,
    )

    if not success:
        return False

    try:
        await member.kick(reason="rainbowl: 面談不合格")
    except discord.HTTPException as exc:
        print(
            "[rainbowl] キックに失敗しました"
            f" guild_id={guild.id} user_id={member.id}"
            f" error={exc}"
        )

    await _delete_applicant_channel(
        guild,
        member.id,
        reason="rainbowl: 面談不合格につき即時削除",
    )

    return True


async def _delete_applicant_channel(
    guild: discord.Guild,
    user_id: int,
    reason: str,
) -> None:
    """DB上のapplicant_channel_idを参照して本人専用チャンネルを削除する。"""
    item = await asyncio.to_thread(
        store.get_item,
        guild.id,
        user_id,
    )

    applicant_channel_id = item.get("applicant_channel_id")

    if applicant_channel_id is None:
        return

    applicant_channel = guild.get_channel(
        int(applicant_channel_id)
    )

    if applicant_channel is None:
        return

    try:
        await applicant_channel.delete(reason=reason)
    except discord.HTTPException as exc:
        print(
            "[rainbowl] 本人専用チャンネルの削除に失敗しました"
            f" guild_id={guild.id} user_id={user_id}"
            f" error={exc}"
        )
