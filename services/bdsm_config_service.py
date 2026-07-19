import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from data.guild_config_store import GuildConfigStore


class BdsmConfigError(Exception):
    """BDSM機能の設定取得・解析に失敗した場合の例外。"""


class BdsmConfigNotFoundError(BdsmConfigError):
    """ギルドにBDSM機能の設定が存在しない場合の例外。"""


@dataclass(frozen=True)
class BdsmGuildConfig:
    """ギルドごとのBDSM機能設定。"""

    guild_id: int
    enabled: bool

    male_url_channel_id: int
    female_url_channel_id: int
    command_log_channel_id: int

    profile_source_channel_ids: Tuple[int, ...]


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
        raise BdsmConfigError(
            "BDSM設定の値が不正です。"
            f" key={key}"
            f" value={value}"
        ) from exc

    if parsed_value <= 0:
        raise BdsmConfigError(
            "BDSM設定の値が不正です。"
            f" key={key}"
            f" value={value}"
        )

    return parsed_value


def _parse_bool(
    value: Any,
    default: bool = True,
) -> bool:
    """Boolean設定を安全に変換する。"""
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        normalized = value.strip().lower()

        if normalized in {"true", "1", "yes", "on"}:
            return True

        if normalized in {"false", "0", "no", "off"}:
            return False

    return default


def _parse_profile_source_channel_ids(
    guild_config: Dict[str, Any],
) -> Tuple[int, ...]:
    """
    既存のprofile設定から、
    プロフィール投稿元チャンネルIDを取得する。
    """
    profile_config = guild_config.get(
        "profile",
        {},
    )

    if not isinstance(profile_config, dict):
        return tuple()

    raw_channel_ids = profile_config.get(
        "profile_source_channel_ids",
        [],
    )

    if not isinstance(raw_channel_ids, list):
        return tuple()

    channel_ids: List[int] = []

    for raw_channel_id in raw_channel_ids:
        try:
            channel_id = int(raw_channel_id)
        except (TypeError, ValueError):
            continue

        if channel_id <= 0:
            continue

        if channel_id in channel_ids:
            continue

        channel_ids.append(channel_id)

    return tuple(channel_ids)


def build_bdsm_config(
    guild_id: int,
    guild_config: Dict[str, Any],
) -> BdsmGuildConfig:
    """
    ギルド設定全体から、
    BDSM機能に必要な設定を生成する。
    """
    bdsm_config = guild_config.get("bdsm")

    if not isinstance(bdsm_config, dict):
        raise BdsmConfigNotFoundError(
            "このサーバーにはBDSM機能が設定されていません。"
        )

    return BdsmGuildConfig(
        guild_id=guild_id,
        enabled=_parse_bool(
            bdsm_config.get("enabled"),
            default=True,
        ),
        male_url_channel_id=_required_positive_int(
            bdsm_config,
            "male_url_channel_id",
        ),
        female_url_channel_id=_required_positive_int(
            bdsm_config,
            "female_url_channel_id",
        ),
        command_log_channel_id=_required_positive_int(
            bdsm_config,
            "command_log_channel_id",
        ),
        profile_source_channel_ids=(
            _parse_profile_source_channel_ids(
                guild_config
            )
        ),
    )


async def get_bdsm_config(
    guild_id: int,
) -> BdsmGuildConfig:
    """
    既存のGuildConfigStoreから、
    ギルドごとのBDSM設定を取得する。
    """
    try:
        # boto3は同期処理なので、
        # Discordのイベントループを止めないよう別スレッドで実行
        guild_config = await asyncio.to_thread(
            _guild_config_store.get_config,
            guild_id,
        )
    except Exception as exc:
        raise BdsmConfigError(
            "ギルド設定の取得に失敗しました。"
        ) from exc

    if not guild_config:
        raise BdsmConfigNotFoundError(
            "このサーバーの設定が見つかりません。"
        )

    if not isinstance(guild_config, dict):
        raise BdsmConfigError(
            "ギルド設定の形式が不正です。"
        )

    return build_bdsm_config(
        guild_id=guild_id,
        guild_config=guild_config,
    )
