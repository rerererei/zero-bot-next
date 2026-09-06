# data/text_daily_store.py

import datetime
import boto3
from decimal import Decimal
from collections import defaultdict
from boto3.dynamodb.conditions import Key


from utils.helpers import jst_now

DYNAMO_REGION = "ap-northeast-1"
TABLE_NAME = "zero_bot_text_daily_stats"

dynamodb = boto3.resource("dynamodb", region_name=DYNAMO_REGION)
table = dynamodb.Table(TABLE_NAME)


def _make_guild_date_key(guild_id: int, date: datetime.date) -> str:
    return f"{guild_id}#{date.isoformat()}"  # "2025-11-30"


def add_daily_text_activity(
    guild_id: int,
    user_id: int,
    *,
    xp: float = 0.0,
    message_count: float = 1.0,
):
    """
    今日の分のテキスト活動（有効投稿数・XP）を日次テーブルに積み上げる。

    text_leveling.py側のクールダウン・文字数フィルタを通過した投稿のみを
    対象とする想定（呼び出し側でXP付与と同じタイミングで呼ぶ）。
    """
    now_jst = jst_now()
    today = now_jst.date()

    pk = _make_guild_date_key(guild_id, today)
    sk = str(user_id)

    table.update_item(
        Key={
            "guild_date": pk,
            "user_id": sk,
        },
        UpdateExpression="""
            SET
              message_count = if_not_exists(message_count, :zero) + :mc,
              xp_total      = if_not_exists(xp_total, :zero) + :xp,
              updated_at    = :updated
        """,
        ExpressionAttributeValues={
            ":mc": Decimal(str(message_count)),
            ":xp": Decimal(str(xp)),
            ":zero": Decimal("0"),
            ":updated": now_jst.isoformat(),
        },
    )


def get_user_total_in_range(
    guild_id: int,
    user_id: int,
    date_from: datetime.date,
    date_to: datetime.date,
) -> dict:
    """
    指定期間 [date_from, date_to] における
    1ユーザーの message_count / xp_total 合計を返す。
    """
    total_message_count = 0.0
    total_xp = 0.0
    sk = str(user_id)

    day = date_from
    while day <= date_to:
        pk = _make_guild_date_key(guild_id, day)
        resp = table.get_item(
            Key={
                "guild_date": pk,
                "user_id": sk,
            }
        )
        item = resp.get("Item")
        if item:
            total_message_count += float(item.get("message_count", 0.0))
            total_xp += float(item.get("xp_total", 0.0))
        day += datetime.timedelta(days=1)

    return {
        "message_count": total_message_count,
        "xp_total": total_xp,
    }


def get_guild_total_in_range(
    guild_id: int,
    date_from: datetime.date,
    date_to: datetime.date,
) -> dict[int, dict]:
    """
    指定期間 [date_from, date_to] のギルド内ユーザー別
    message_count / xp_total 合計。
    戻り値: { user_id(int): {"message_count": float, "xp_total": float} }
    """
    totals: dict[int, dict] = defaultdict(
        lambda: {"message_count": 0.0, "xp_total": 0.0}
    )

    day = date_from
    while day <= date_to:
        pk = _make_guild_date_key(guild_id, day)

        resp = table.query(
            KeyConditionExpression=Key("guild_date").eq(pk)
        )
        items = resp.get("Items", [])

        for item in items:
            try:
                uid = int(item["user_id"])
            except (KeyError, ValueError, TypeError):
                continue

            totals[uid]["message_count"] += float(
                item.get("message_count", 0.0)
            )
            totals[uid]["xp_total"] += float(item.get("xp_total", 0.0))

        day += datetime.timedelta(days=1)

    return dict(totals)
