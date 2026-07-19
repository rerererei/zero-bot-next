import asyncio

from services.bdsm_service import (
    BdsmMatchError,
    fetch_match_score,
)


RESULT_ID = "raxsP7pX"
PARTNER_ID = "rFUAX2Ze"


async def main() -> None:
    try:
        score = await fetch_match_score(
            RESULT_ID,
            PARTNER_ID,
        )
        print(f"相性スコア: {score}%")

    except ValueError as exc:
        print(f"入力エラー: {exc}")

    except BdsmMatchError as exc:
        print(f"取得エラー: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
