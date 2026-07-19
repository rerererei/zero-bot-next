import asyncio
import json
from typing import Any, Dict, Optional

import aiohttp


MATCH_URL = "https://bdsmtest.org/ajax/match"
REQUEST_TIMEOUT_SECONDS = 15


class BdsmMatchError(Exception):
    """BDSM相性診断の取得に失敗した場合の例外。"""


class BdsmResultNotFoundError(BdsmMatchError):
    """指定された診断結果が存在しない場合の例外。"""


class BdsmRateLimitError(BdsmMatchError):
    """相性診断APIのレート制限が発生した場合の例外。"""


async def fetch_match_score(
    result_id: str,
    partner_id: str,
    session: Optional[aiohttp.ClientSession] = None,
) -> int:
    """
    2つのBDSM診断結果IDから相性スコアを取得する。

    Args:
        result_id:
            比較元ユーザーの診断結果ID。

        partner_id:
            比較対象ユーザーの診断結果ID。

        session:
            再利用するaiohttp.ClientSession。

    Returns:
        0～100の相性スコア。
    """
    normalized_result_id = result_id.strip()
    normalized_partner_id = partner_id.strip()

    if (
        not normalized_result_id
        or not normalized_partner_id
    ):
        raise ValueError(
            "診断結果IDを2つ指定してください。"
        )

    if session is not None:
        return await _request_match_score(
            session=session,
            result_id=normalized_result_id,
            partner_id=normalized_partner_id,
        )

    timeout = aiohttp.ClientTimeout(
        total=REQUEST_TIMEOUT_SECONDS,
    )

    try:
        async with aiohttp.ClientSession(
            timeout=timeout
        ) as created_session:
            return await _request_match_score(
                session=created_session,
                result_id=normalized_result_id,
                partner_id=normalized_partner_id,
            )

    except BdsmMatchError:
        raise

    except asyncio.TimeoutError as exc:
        raise BdsmMatchError(
            "相性診断APIとの通信がタイムアウトしました。"
        ) from exc

    except aiohttp.ClientError as exc:
        raise BdsmMatchError(
            "相性診断APIとの通信に失敗しました。"
        ) from exc


async def _request_match_score(
    session: aiohttp.ClientSession,
    result_id: str,
    partner_id: str,
) -> int:
    """BDSM相性診断APIへリクエストする。"""
    payload = {
        "rauth[rid]": result_id,
        "partner": partner_id,
    }

    try:
        async with session.post(
            MATCH_URL,
            data=payload,
        ) as response:
            response_body = await response.text()

            if response.status == 404:
                raise BdsmResultNotFoundError(
                    "指定された診断結果が見つかりません。"
                )

            if response.status == 429:
                raise BdsmRateLimitError(
                    "相性診断APIの利用回数制限に達しました。"
                )

            if response.status != 200:
                raise BdsmMatchError(
                    "相性診断APIでエラーが発生しました。"
                    f" status={response.status}"
                )

    except BdsmMatchError:
        raise

    except asyncio.TimeoutError as exc:
        raise BdsmMatchError(
            "相性診断APIとの通信がタイムアウトしました。"
        ) from exc

    except aiohttp.ClientError as exc:
        raise BdsmMatchError(
            "相性診断APIとの通信に失敗しました。"
        ) from exc

    try:
        result: Dict[str, Any] = json.loads(
            response_body
        )
    except json.JSONDecodeError as exc:
        raise BdsmMatchError(
            "相性診断APIのレスポンスを解析できませんでした。"
        ) from exc

    score = _extract_score(result)

    if not 0 <= score <= 100:
        raise BdsmMatchError(
            "相性スコアの値が不正です。"
            f" score={score}"
        )

    return score


def _extract_score(
    result: Dict[str, Any],
) -> int:
    """
    APIレスポンスから相性スコアを取得する。

    以下の両形式に対応する。

    {
        "score": 95,
        "partner": "rFUAX2Ze"
    }

    {
        "ok": true,
        "response": {
            "score": 95,
            "partner": "rFUAX2Ze"
        }
    }
    """
    direct_score = result.get("score")

    if (
        isinstance(direct_score, int)
        and not isinstance(direct_score, bool)
    ):
        return direct_score

    response_data = result.get("response")

    if isinstance(response_data, dict):
        nested_score = response_data.get(
            "score"
        )

        if (
            isinstance(nested_score, int)
            and not isinstance(
                nested_score,
                bool,
            )
        ):
            return nested_score

    raise BdsmMatchError(
        "相性スコアを取得できませんでした。"
        f" response={result}"
    )
