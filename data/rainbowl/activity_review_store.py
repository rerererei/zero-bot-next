# data/rainbowl/activity_review_store.py

from decimal import Decimal
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError


def _from_decimal(value):
    """DynamoDB から読んだ値を Python の型（int/float/list/dict）に戻す"""
    if isinstance(value, Decimal):
        if value % 1 == 0:
            return int(value)
        return float(value)
    if isinstance(value, list):
        return [_from_decimal(v) for v in value]
    if isinstance(value, dict):
        return {k: _from_decimal(v) for k, v in value.items()}
    return value


class ActivityReviewStore:
    """
    rainbowl機能専用。月次活動整理（11章）の集計バッチが対象月を
    すでに処理したかどうかを、zero_bot_rainbowl_activity_review
    テーブルへ読み書きする（ギルド+月単位の冪等性ガードのみを扱う）。

    個別ユーザーの状態（在籍確認の進行状況・休止申請・プロフィール等）は
    data/rainbowl/member_state_store.py の
    zero_bot_rainbowl_member_state テーブル側で扱う。

    使用テーブル構造：
    - パーティションキー: guild_id  (String)
    - ソートキー        : sort_key (String) … "BATCH#{target_month}" 固定

    target_monthは "YYYY-MM" 形式（例: "2026-07"）。
    """

    def __init__(
        self,
        table_name: str = "zero_bot_rainbowl_activity_review",
        region: str = "ap-northeast-1",
    ):
        self.table_name = table_name
        self.dynamodb = boto3.resource(
            "dynamodb",
            region_name=region,
        )
        self.table = self.dynamodb.Table(table_name)

    # =============================
    #    内部キー生成
    # =============================
    def _batch_key(
        self,
        guild_id: int,
        target_month: str,
    ) -> Dict[str, str]:
        return {
            "guild_id": str(guild_id),
            "sort_key": f"BATCH#{target_month}",
        }

    # =============================
    #    月次バッチの処理権（冪等性ガード）
    # =============================
    def claim_monthly_batch(
        self,
        guild_id: int,
        target_month: str,
        now_iso: str,
        stale_before_iso: str,
    ) -> bool:
        """
        対象月の集計バッチが未着手の場合、または前回IN_PROGRESSのまま
        stale_before_isoより古く残っている場合（異常終了とみなせる場合）
        のみ、処理権を獲得してIN_PROGRESSにする。

        成功した場合はTrue、既に他プロセスが処理中／完了済みの場合は
        Falseを返す。
        """
        try:
            self.table.put_item(
                Item=self._batch_key(guild_id, target_month) | {
                    "status": "IN_PROGRESS",
                    "started_at": now_iso,
                },
                ConditionExpression=(
                    "attribute_not_exists(sort_key) OR "
                    "(#status = :in_progress AND started_at < :stale_before)"
                ),
                ExpressionAttributeNames={
                    "#status": "status",
                },
                ExpressionAttributeValues={
                    ":in_progress": "IN_PROGRESS",
                    ":stale_before": stale_before_iso,
                },
            )
            return True

        except ClientError as exc:
            if (
                exc.response["Error"]["Code"]
                == "ConditionalCheckFailedException"
            ):
                return False
            raise

    def finish_monthly_batch(
        self,
        guild_id: int,
        target_month: str,
        now_iso: str,
    ) -> None:
        """集計バッチの完了をDONEとして記録する。"""
        self.table.update_item(
            Key=self._batch_key(guild_id, target_month),
            UpdateExpression=(
                "SET #status = :done, finished_at = :now"
            ),
            ExpressionAttributeNames={
                "#status": "status",
            },
            ExpressionAttributeValues={
                ":done": "DONE",
                ":now": now_iso,
            },
        )

    def get_monthly_batch_state(
        self,
        guild_id: int,
        target_month: str,
    ) -> Optional[Dict[str, Any]]:
        resp = self.table.get_item(
            Key=self._batch_key(guild_id, target_month)
        )
        item = resp.get("Item")
        return _from_decimal(item) if item else None
