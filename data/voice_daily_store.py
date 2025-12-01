# data/voice_daily_store.py

import datetime
import boto3
from decimal import Decimal
from collections import defaultdict
from boto3.dynamodb.conditions import Key


from utils.time import jst_now  # なければ後で書く or 直接 datetime＋pytz 使う

DYNAMO_REGION = "ap-northeast-1"
TABLE_NAME = "zero_bot_voice_daily_stats"

dynamodb = boto3.resource("dynamodb", region_name=DYNAMO_REGION)
table = dynamodb.Table(TABLE_NAME)


def _make_guild_date_key(guild_id: int, date: datetime.date) -> str:
    return f"{guild_id}#{date.isoformat()}"  # "2025-11-30"


def add_daily_voice_minutes(
    guild_id: int,
    user_id: int,
    *,
    total_min: float = 0.0,
    solo_min: float = 0.0,
    small_group_min: float = 0.0,
    mid_group_min: float = 0.0,
    big_group_min: float = 0.0,
    muted_min: float = 0.0,
):
    """
    今日の分のVC滞在時間（分）を日次テーブルに積み上げる。

    通話スナップショットの1tickごとに呼ぶ想定：
      add_daily_voice_minutes(..., total_min=1.0, small_group_min=1.0, ...)
    みたいなノリ。
    """
    now_jst = datetime.datetime.now(datetime.timezone.utc).astimezone(
        datetime.timezone(datetime.timedelta(hours=9))
    )
    today = now_jst.date()

    pk = _make_guild_date_key(guild_id, today)
    sk = str(user_id)

    # UpdateExpression で積み上げ
    expr_attr_values = {
        ":t": Decimal(str(total_min)),
        ":s": Decimal(str(solo_min)),
        ":sg": Decimal(str(small_group_min)),
        ":mg": Decimal(str(mid_group_min)),
        ":bg": Decimal(str(big_group_min)),
        ":m": Decimal(str(muted_min)),
        ":zero": Decimal("0"),
        ":updated": now_jst.isoformat(),
    }

    table.update_item(
        Key={
            "guild_date": pk,
            "user_id": sk,
        },
        UpdateExpression="""
            SET
              total_min       = if_not_exists(total_min, :zero) + :t,
              solo_min        = if_not_exists(solo_min, :zero) + :s,
              small_group_min = if_not_exists(small_group_min, :zero) + :sg,
              mid_group_min   = if_not_exists(mid_group_min, :zero) + :mg,
              big_group_min   = if_not_exists(big_group_min, :zero) + :bg,
              muted_min       = if_not_exists(muted_min, :zero) + :m,
              updated_at      = :updated
        """,
        ExpressionAttributeValues=expr_attr_values,
    )

def get_user_total_minutes_in_range(
    guild_id: int,
    user_id: int,
    date_from: datetime.date,
    date_to: datetime.date,
) -> float:
    """
    指定期間 [date_from, date_to] における
    1ユーザーの total_min 合計（分）を返す。
    """
    total = 0.0
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
        if item and "total_min" in item:
            total += float(item["total_min"])
        day += datetime.timedelta(days=1)

    return total


def get_guild_total_minutes_in_range(
    guild_id: int,
    date_from: datetime.date,
    date_to: datetime.date,
) -> dict[int, float]:
    """
    指定期間 [date_from, date_to] のギルド内ユーザー別 total_min 合計。
    戻り値: { user_id(int): total_min(float) }
    """
    totals: dict[int, float] = defaultdict(float)

    day = date_from
    while day <= date_to:
        pk = _make_guild_date_key(guild_id, day)

        # その日のギルド分を全員ぶんQuery
        resp = table.query(
            KeyConditionExpression=Key("guild_date").eq(pk)
        )
        items = resp.get("Items", [])

        for item in items:
            try:
                uid = int(item["user_id"])
            except (KeyError, ValueError, TypeError):
                continue

            total_min = float(item.get("total_min", 0.0))
            totals[uid] += total_min

        day += datetime.timedelta(days=1)

    return dict(totals)
