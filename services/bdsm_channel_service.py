import re
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Set

import discord


BDSM_RESULT_URL_PATTERN = re.compile(
    r"https?://(?:www\.)?bdsmtest\.org/r/"
    r"([A-Za-z0-9]+)(?=$|[^A-Za-z0-9])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BdsmResultEntry:
    """BDSM診断結果URLの投稿情報。"""

    user_id: int
    display_name: str
    result_id: str
    result_url: str
    message_url: str
    created_at: datetime


def extract_bdsm_result_id(
    content: str,
) -> Optional[str]:
    """
    メッセージ本文から診断結果IDを抽出する。

    例:
        https://bdsmtest.org/r/raxsP7pX
        -> raxsP7pX
    """
    if not content:
        return None

    match = BDSM_RESULT_URL_PATTERN.search(
        content
    )

    if match is None:
        return None

    return match.group(1)


def _create_result_entry(
    message: discord.Message,
    result_id: str,
) -> BdsmResultEntry:
    """Discordメッセージから診断結果情報を生成する。"""
    display_name = getattr(
        message.author,
        "display_name",
        message.author.name,
    )

    return BdsmResultEntry(
        user_id=message.author.id,
        display_name=display_name,
        result_id=result_id,
        result_url=(
            f"https://bdsmtest.org/r/{result_id}"
        ),
        message_url=message.jump_url,
        created_at=message.created_at,
    )


async def collect_latest_results(
    channel: discord.TextChannel,
    exclude_user_id: Optional[int] = None,
) -> List[BdsmResultEntry]:
    """
    チャンネル内の診断結果を取得する。

    同じユーザーが複数回投稿している場合は、
    最新の有効なURLだけを使用する。
    """
    results: Dict[int, BdsmResultEntry] = {}

    async for message in channel.history(
        limit=None,
        oldest_first=False,
    ):
        if message.author.bot:
            continue

        if (
            exclude_user_id is not None
            and message.author.id
            == exclude_user_id
        ):
            continue

        # 新しいメッセージから取得しているため、
        # すでに登録済みなら古い投稿は確認しない
        if message.author.id in results:
            continue

        result_id = extract_bdsm_result_id(
            message.content
        )

        if result_id is None:
            continue

        results[message.author.id] = (
            _create_result_entry(
                message=message,
                result_id=result_id,
            )
        )

    return list(results.values())


async def find_latest_user_result(
    channels: Iterable[discord.TextChannel],
    user_id: int,
) -> Optional[BdsmResultEntry]:
    """
    複数の診断URLチャンネルから、
    指定ユーザーの最新診断結果を取得する。
    """
    latest_result: Optional[
        BdsmResultEntry
    ] = None

    for channel in channels:
        async for message in channel.history(
            limit=None,
            oldest_first=False,
        ):
            if message.author.bot:
                continue

            if message.author.id != user_id:
                continue

            result_id = extract_bdsm_result_id(
                message.content
            )

            if result_id is None:
                continue

            entry = _create_result_entry(
                message=message,
                result_id=result_id,
            )

            if (
                latest_result is None
                or entry.created_at
                > latest_result.created_at
            ):
                latest_result = entry

            # このチャンネルでは最新の有効投稿を取得済み
            break

    return latest_result


async def collect_latest_profile_urls(
    channels: Iterable[discord.TextChannel],
    user_ids: Set[int],
) -> Dict[int, str]:
    """
    複数のプロフィールチャンネルから、
    対象ユーザー本人の最新プロフィール投稿URLを取得する。

    Returns:
        {
            DiscordユーザーID: プロフィール投稿URL
        }
    """
    if not user_ids:
        return {}

    latest_message_by_user: Dict[
        int,
        discord.Message,
    ] = {}

    for channel in channels:
        # このチャンネル内ですでに取得したユーザー
        found_user_ids: Set[int] = set()

        async for message in channel.history(
            limit=None,
            oldest_first=False,
        ):
            user_id = message.author.id

            if user_id not in user_ids:
                continue

            # 新しい順に検索しているため、
            # 同一チャンネル内では最初の1件だけ取得
            if user_id in found_user_ids:
                continue

            found_user_ids.add(user_id)

            current_message = (
                latest_message_by_user.get(
                    user_id
                )
            )

            if (
                current_message is None
                or message.created_at
                > current_message.created_at
            ):
                latest_message_by_user[
                    user_id
                ] = message

            if found_user_ids == user_ids:
                break

    return {
        user_id: message.jump_url
        for user_id, message
        in latest_message_by_user.items()
    }
