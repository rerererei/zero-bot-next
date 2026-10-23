# data/voice_daily_store.py

import datetime
import boto3
from decimal import Decimal
from collections import defaultdict
from boto3.dynamodb.conditions import Key


from utils.helpers import jst_now

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
    xp: float = 0.0,
):
    """
    今日の分のVC滞在時間（分）を日次テーブルに積み上げる。
    xpは付与したVC XP（rainbowlの月次活動判定で使う。汎用側は0のまま）。

    通話スナップショットの1tickごとに呼ぶ想定：
      add_daily_voice_minutes(..., total_min=1.0, small_group_min=1.0, ...)
    みたいなノリ。
    """
    now_jst = jst_now()
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
        ":xp": Decimal(str(xp)),
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
              xp_total        = if_not_exists(xp_total, :zero) + :xp,
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


def _estimate_voice_xp(item: dict) -> float:
    """
    xp_totalを記録する前の日次レコード用に、人数別の滞在分数から
    rainbowlのVC XP（0.3 × 人数ボーナス × ミュート倍率）を概算する。
    ミュート分数は人数別に分かれていないため、2〜3人相当として差し引く。
    """
    base = 0.3
    xp = base * (
        0.8 * float(item.get("solo_min", 0.0))
        + 1.2 * float(item.get("small_group_min", 0.0))
        + 1.5 * float(item.get("mid_group_min", 0.0))
        + 2.0 * float(item.get("big_group_min", 0.0))
    )
    xp -= base * 1.2 * 0.5 * float(item.get("muted_min", 0.0))
    return max(xp, 0.0)


def get_guild_xp_in_range(
    guild_id: int,
    date_from: datetime.date,
    date_to: datetime.date,
) -> dict[int, float]:
    """
    指定期間 [date_from, date_to] のギルド内ユーザー別 VC XP 合計。
    xp_totalが無い日（記録開始前）は滞在分数から概算する。
    戻り値: { user_id(int): xp(float) }
    """
    totals: dict[int, float] = defaultdict(float)

    day = date_from
    while day <= date_to:
        pk = _make_guild_date_key(guild_id, day)
        query_kwargs = {
            "KeyConditionExpression": Key("guild_date").eq(pk),
        }

        while True:
            resp = table.query(**query_kwargs)

            for item in resp.get("Items", []):
                try:
                    uid = int(item["user_id"])
                except (KeyError, ValueError, TypeError):
                    continue

                if "xp_total" in item:
                    totals[uid] += float(item["xp_total"])
                else:
                    totals[uid] += _estimate_voice_xp(item)

            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            query_kwargs["ExclusiveStartKey"] = last_key

        day += datetime.timedelta(days=1)

    return dict(totals)
