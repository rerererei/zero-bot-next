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
    passed_notice_channel_id: int

    reception_emoji_id: int
    reception_emoji_name: str


# 既存のギルド設定ストアを使い回す
_guild_config_store = GuildConfigStore()


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
        passed_notice_channel_id=_required_positive_int(
            rainbowl_config,
            "passed_notice_channel_id",
        ),
        reception_emoji_id=_required_positive_int(
            rainbowl_config,
            "reception_emoji_id",
        ),
        reception_emoji_name=_required_str(
            rainbowl_config,
            "reception_emoji_name",
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
