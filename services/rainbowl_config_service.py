import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from data.guild_config_store import GuildConfigStore


class RainbowlConfigError(Exception):
    """rainbowl機能の設定取得・解析に失敗した場合の例外。"""


class RainbowlConfigNotFoundError(RainbowlConfigError):
    """ギルドにrainbowl機能の設定が存在しない場合の例外。"""


@dataclass(frozen=True)
class RainbowlGuildConfig:
    """ギルドごとのrainbowl機能設定。"""

    guild_id: int

    entrant_role_id: int
    applicant_role_id: int
    passed_role_id: int
    acknowledged_role_id: int
    newcomer_role_id: int
    member_role_id: int
    staff_role_id: int

    onboarding_category_id: int
    interview_category_id: int
    review_category_id: int

    onboarding_channel_ids: Tuple[int, ...]

    interview_info_channel_id: int
    interview_voice_channel_id: int

    review_profiles_channel_id: int
    review_notes_channel_id: int
    review_results_channel_id: int

    join_log_channel_id: int
    role_button_channel_id: int
    male_profile_channel_id: int
    female_profile_channel_id: int

    reception_emoji_id: int
    reception_emoji_name: str

    # 管理者と同様に扱うロール（任意。未設定なら空）。自動キック・月次活動確認の
    # 対象外になり、運営専用ボタンも押せる。is_admin_like() を参照。
    admin_role_ids: Tuple[int, ...] = ()

    # 規約同意（任意。未設定なら規約同意の判定は行わない）。
    # 了承済みの人が agreement_messages のすべてに⭕を押すと agreed_role_id を付与し、
    # ロール付与チャンネルが見えるようになる（cogs/rainbowl_rules_agreement.py）。
    agreed_role_id: int = 0
    # (チャンネルID, メッセージID) の組
    agreement_messages: Tuple[Tuple[int, int], ...] = ()


def is_admin_like(
    member: Any,
    config: "RainbowlGuildConfig",
) -> bool:
    """
    管理者と同様に扱うメンバーか。
    Administrator権限・運営ロール・admin_role_idsのいずれかを持っていればTrue。
    """
    if member.guild_permissions.administrator:
        return True

    role_ids = {role.id for role in member.roles}

    return (
        config.staff_role_id in role_ids
        or bool(role_ids & set(config.admin_role_ids))
    )


# 既存のギルド設定ストアを使い回す
_guild_config_store = GuildConfigStore()


def _optional_int_tuple(
    data: Dict[str, Any],
    key: str,
) -> Tuple[int, ...]:
    """任意のIDリストを取得する（未設定なら空タプル）。"""
    try:
        return tuple(int(x) for x in data.get(key) or [])
    except (TypeError, ValueError) as exc:
        raise RainbowlConfigError(
            "rainbowl設定の値が不正です。"
            f" key={key}"
            f" value={data.get(key)}"
        ) from exc


def _optional_positive_int(
    data: Dict[str, Any],
    key: str,
) -> int:
    """任意の正整数を取得する（未設定なら0）。"""
    if not data.get(key):
        return 0
    return _required_positive_int(data, key)


def _optional_channel_message_pairs(
    data: Dict[str, Any],
    key: str,
) -> Tuple[Tuple[int, int], ...]:
    """{チャンネルID: メッセージID} の辞書を (チャンネルID, メッセージID) の組にする（未設定なら空）。"""
    raw = data.get(key) or {}
    try:
        return tuple(
            (int(channel_id), int(message_id))
            for channel_id, message_id in raw.items()
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise RainbowlConfigError(
            "rainbowl設定の値が不正です。"
            f" key={key}"
            f" value={raw}"
        ) from exc


def _required_positive_int(
    data: Dict[str, Any],
    key: str,
) -> int:
    """
    設定辞書から必須の正整数を取得する。

    DiscordのIDはDynamoDB上では文字列で保存されているため、
    利用時にintへ変換する。
    """
    value = data.get(key)

    try:
        parsed_value = int(value)
    except (TypeError, ValueError) as exc:
        raise RainbowlConfigError(
            "rainbowl設定の値が不正です。"
            f" key={key}"
            f" value={value}"
        ) from exc

    if parsed_value <= 0:
        raise RainbowlConfigError(
            "rainbowl設定の値が不正です。"
            f" key={key}"
            f" value={value}"
        )

    return parsed_value


def _required_str(
    data: Dict[str, Any],
    key: str,
) -> str:
    """設定辞書から必須の文字列を取得する。"""
    value = data.get(key)

    if not isinstance(value, str) or not value.strip():
        raise RainbowlConfigError(
            "rainbowl設定の値が不正です。"
            f" key={key}"
            f" value={value}"
        )

    return value.strip()


def _required_channel_id_list(
    data: Dict[str, Any],
    key: str,
) -> Tuple[int, ...]:
    """設定辞書から必須のチャンネルIDリストを取得する。"""
    raw_channel_ids = data.get(key)

    if (
        not isinstance(raw_channel_ids, list)
        or not raw_channel_ids
    ):
        raise RainbowlConfigError(
            "rainbowl設定の値が不正です。"
            f" key={key}"
            f" value={raw_channel_ids}"
        )

    channel_ids: List[int] = []

    for raw_channel_id in raw_channel_ids:
        try:
            channel_id = int(raw_channel_id)
        except (TypeError, ValueError) as exc:
            raise RainbowlConfigError(
                "rainbowl設定の値が不正です。"
                f" key={key}"
                f" value={raw_channel_ids}"
            ) from exc

        if channel_id <= 0:
            raise RainbowlConfigError(
                "rainbowl設定の値が不正です。"
                f" key={key}"
                f" value={raw_channel_ids}"
            )

        channel_ids.append(channel_id)

    return tuple(channel_ids)


def build_rainbowl_config(
    guild_id: int,
    guild_config: Dict[str, Any],
) -> RainbowlGuildConfig:
    """
    ギルド設定全体から、
    rainbowl機能に必要な設定を生成する。
    """
    rainbowl_config = guild_config.get("rainbowl")

    if not isinstance(rainbowl_config, dict):
        raise RainbowlConfigNotFoundError(
            "このサーバーにはrainbowl機能が設定されていません。"
        )

    return RainbowlGuildConfig(
        guild_id=guild_id,
        entrant_role_id=_required_positive_int(
            rainbowl_config,
            "entrant_role_id",
        ),
        applicant_role_id=_required_positive_int(
            rainbowl_config,
            "applicant_role_id",
        ),
        passed_role_id=_required_positive_int(
            rainbowl_config,
            "passed_role_id",
        ),
        acknowledged_role_id=_required_positive_int(
            rainbowl_config,
            "acknowledged_role_id",
        ),
        newcomer_role_id=_required_positive_int(
            rainbowl_config,
            "newcomer_role_id",
        ),
        member_role_id=_required_positive_int(
            rainbowl_config,
            "member_role_id",
        ),
        staff_role_id=_required_positive_int(
            rainbowl_config,
            "staff_role_id",
        ),
        onboarding_category_id=_required_positive_int(
            rainbowl_config,
            "onboarding_category_id",
        ),
        interview_category_id=_required_positive_int(
            rainbowl_config,
            "interview_category_id",
        ),
        review_category_id=_required_positive_int(
            rainbowl_config,
            "review_category_id",
        ),
        onboarding_channel_ids=_required_channel_id_list(
            rainbowl_config,
            "onboarding_channel_ids",
        ),
        interview_info_channel_id=_required_positive_int(
            rainbowl_config,
            "interview_info_channel_id",
        ),
        interview_voice_channel_id=_required_positive_int(
            rainbowl_config,
            "interview_voice_channel_id",
        ),
        review_profiles_channel_id=_required_positive_int(
            rainbowl_config,
            "review_profiles_channel_id",
        ),
        review_notes_channel_id=_required_positive_int(
            rainbowl_config,
            "review_notes_channel_id",
        ),
        review_results_channel_id=_required_positive_int(
            rainbowl_config,
            "review_results_channel_id",
        ),
        join_log_channel_id=_required_positive_int(
            rainbowl_config,
            "join_log_channel_id",
        ),
        role_button_channel_id=_required_positive_int(
            rainbowl_config,
            "role_button_channel_id",
        ),
        male_profile_channel_id=_required_positive_int(
            rainbowl_config,
            "male_profile_channel_id",
        ),
        female_profile_channel_id=_required_positive_int(
            rainbowl_config,
            "female_profile_channel_id",
        ),
        reception_emoji_id=_required_positive_int(
            rainbowl_config,
            "reception_emoji_id",
        ),
        reception_emoji_name=_required_str(
            rainbowl_config,
            "reception_emoji_name",
        ),
        admin_role_ids=_optional_int_tuple(
            rainbowl_config,
            "admin_role_ids",
        ),
        agreed_role_id=_optional_positive_int(
            rainbowl_config,
            "agreed_role_id",
        ),
        agreement_messages=_optional_channel_message_pairs(
            rainbowl_config,
            "agreement_messages",
        ),
    )


async def get_rainbowl_config(
    guild_id: int,
) -> RainbowlGuildConfig:
    """
    既存のGuildConfigStoreから、
    ギルドごとのrainbowl設定を取得する。
    """
    try:
        # boto3は同期処理なので、
        # Discordのイベントループを止めないよう別スレッドで実行
        guild_config = await asyncio.to_thread(
            _guild_config_store.get_config,
            guild_id,
        )
    except Exception as exc:
        raise RainbowlConfigError(
            "ギルド設定の取得に失敗しました。"
        ) from exc

    if not guild_config:
        raise RainbowlConfigNotFoundError(
            "このサーバーの設定が見つかりません。"
        )

    if not isinstance(guild_config, dict):
        raise RainbowlConfigError(
            "ギルド設定の形式が不正です。"
        )

    return build_rainbowl_config(
        guild_id=guild_id,
        guild_config=guild_config,
    )


async def set_rainbowl_fields(
    guild_id: int,
    fields: Dict[str, Any],
) -> None:
    """
    rainbowl名前空間の中の指定キーだけを書き換える
    （他のキー・他の名前空間は読み直して上書きしないので壊さない）。
    """
    names = {"#rainbowl": "rainbowl"}
    values = {}
    parts = []
    for index, (key, value) in enumerate(fields.items()):
        names[f"#k{index}"] = key
        values[f":v{index}"] = value
        parts.append(f"#rainbowl.#k{index} = :v{index}")

    await asyncio.to_thread(
        _guild_config_store.table.update_item,
        Key={"guild_id": str(guild_id)},
        UpdateExpression="SET " + ", ".join(parts),
        ConditionExpression="attribute_exists(#rainbowl)",
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )
