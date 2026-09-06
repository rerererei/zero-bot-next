# data/rainbowl/member_state_store.py

from decimal import Decimal
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError


def _to_decimal(value):
    """Pythonの数値 → DynamoDB対応のDecimalに変換"""
    if isinstance(value, float) or isinstance(value, int):
        return Decimal(str(value))
    elif isinstance(value, dict):
        return {k: _to_decimal(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_to_decimal(v) for v in value]
    return value


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


class MemberStateStore:
    """
    rainbowl機能専用。入会後メンバーの継続的な状態（在籍確認・休止申請・
    個別除外・入会後プロフィール）を、zero_bot_rainbowl_member_state
    テーブルへ読み書きする。

    入場〜面談合否フローの応募者状態（RainbowlStore／
    zero_bot_rainbowl_applicants）や、月次バッチの処理済みガード
    （ActivityReviewStore／zero_bot_rainbowl_activity_review）とは別物。
    こちらは合格後のメンバーについて、guild_id+user_idで1件だけ持つ
    「現在の状態」を管理する（applicants_storeと同じく、履歴は配列で
    アーカイブし、レコードは削除しない）。

    使用テーブル構造：
    - パーティションキー: guild_id (String)
    - ソートキー        : user_id  (String)

    Item形式（主なフィールド）：
    {
        "guild_id": "123",
        "user_id": "456",
        "membership_status": "ACTIVE" | "KICK_PENDING" | "KICKED",
        "pause": {
            "is_paused": bool,
            "reason": str,
            "requested_at": ISO8601,
            "resume_at": ISO8601 | None,
        },
        "exempt": {
            "is_exempt": bool,
            "reason": str,
            "granted_by": "789",
            "granted_at": ISO8601,
        },
        "current_review": {  # 進行中の月次在籍確認サイクル（無ければ属性自体が無い）
            "target_month": "2026-07",
            "status": "PENDING",
            "text_activity": ...,
            "vc_activity": ...,
            "score": ...,
            "confirm_deadline": ISO8601,
        },
        "review_history": [ ... ],  # 過去サイクルのアーカイブ
        "initial_profile": {  # 入会後プロフィールの初回記載（一度登録されたら不変）
            "content": str,
            "message_id": "999" | None,
            "recorded_at": ISO8601,
        },
        "current_profile": {  # 入会後プロフィールの最新版（更新のたびに上書き）
            "content": str,
            "message_id": "999" | None,
            "updated_at": ISO8601,
        },
        "updated_at": ISO8601,
    }
    """

    def __init__(
        self,
        table_name: str = "zero_bot_rainbowl_member_state",
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
    def _key(
        self,
        guild_id: int,
        user_id: int,
    ) -> Dict[str, str]:
        return {
            "guild_id": str(guild_id),
            "user_id": str(user_id),
        }

    # =============================
    #    1件取得
    # =============================
    def get_item(
        self,
        guild_id: int,
        user_id: int,
    ) -> Dict[str, Any]:
        resp = self.table.get_item(Key=self._key(guild_id, user_id))
        return _from_decimal(resp.get("Item", {}))

    # =============================
    #    入会後プロフィール
    # =============================
    def record_initial_profile(
        self,
        guild_id: int,
        user_id: int,
        content: str,
        message_id: Optional[int],
        now_iso: str,
    ) -> bool:
        """
        入会後プロフィールの初回記載を登録する。initial_profileが
        未登録の場合のみ動作し、current_profileにも同じ内容を
        初期値としてセットする（レコード自体が無ければ新規作成する）。

        成功した場合はTrue、既に初回記載が登録済みの場合はFalseを返す。
        """
        profile = _to_decimal(
            {
                "content": content,
                "message_id": (
                    str(message_id) if message_id is not None else None
                ),
                "recorded_at": now_iso,
            }
        )

        try:
            self.table.update_item(
                Key=self._key(guild_id, user_id),
                UpdateExpression=(
                    "SET initial_profile = :profile, "
                    "current_profile = :current_profile, "
                    "membership_status = if_not_exists("
                    "membership_status, :active), "
                    "updated_at = :now"
                ),
                ConditionExpression="attribute_not_exists(initial_profile)",
                ExpressionAttributeValues={
                    ":profile": profile,
                    ":current_profile": {
                        **profile,
                        "updated_at": now_iso,
                    },
                    ":active": "ACTIVE",
                    ":now": now_iso,
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

    def update_current_profile(
        self,
        guild_id: int,
        user_id: int,
        content: str,
        message_id: Optional[int],
        now_iso: str,
    ) -> None:
        """
        入会後プロフィールの最新版を更新する。編集のたびに呼ぶ想定で、
        呼ぶたびに無条件で上書きする（initial_profileは変更しない）。
        """
        self.table.update_item(
            Key=self._key(guild_id, user_id),
            UpdateExpression=(
                "SET current_profile = :profile, updated_at = :now"
            ),
            ExpressionAttributeValues={
                ":profile": _to_decimal(
                    {
                        "content": content,
                        "message_id": (
                            str(message_id)
                            if message_id is not None
                            else None
                        ),
                        "updated_at": now_iso,
                    }
                ),
                ":now": now_iso,
            },
        )

    # =============================
    #    活動休止申請
    # =============================
    def set_pause(
        self,
        guild_id: int,
        user_id: int,
        reason: str,
        resume_at: Optional[str],
        now_iso: str,
    ) -> None:
        """活動休止申請を登録・更新する（無条件で上書き）。"""
        self.table.update_item(
            Key=self._key(guild_id, user_id),
            UpdateExpression="SET #pause = :pause, updated_at = :now",
            ExpressionAttributeNames={"#pause": "pause"},
            ExpressionAttributeValues={
                ":pause": {
                    "is_paused": True,
                    "reason": reason,
                    "requested_at": now_iso,
                    "resume_at": resume_at,
                },
                ":now": now_iso,
            },
        )

    def clear_pause(
        self,
        guild_id: int,
        user_id: int,
        now_iso: str,
    ) -> None:
        """活動休止を解除する。"""
        self.table.update_item(
            Key=self._key(guild_id, user_id),
            UpdateExpression="SET #pause = :pause, updated_at = :now",
            ExpressionAttributeNames={"#pause": "pause"},
            ExpressionAttributeValues={
                ":pause": {
                    "is_paused": False,
                    "reason": None,
                    "requested_at": None,
                    "resume_at": None,
                },
                ":now": now_iso,
            },
        )

    # =============================
    #    個別除外登録
    # =============================
    def set_exempt(
        self,
        guild_id: int,
        user_id: int,
        reason: str,
        granted_by: int,
        now_iso: str,
    ) -> None:
        """運営による月次活動判定の個別除外を登録する。"""
        self.table.update_item(
            Key=self._key(guild_id, user_id),
            UpdateExpression="SET exempt = :exempt, updated_at = :now",
            ExpressionAttributeValues={
                ":exempt": {
                    "is_exempt": True,
                    "reason": reason,
                    "granted_by": str(granted_by),
                    "granted_at": now_iso,
                },
                ":now": now_iso,
            },
        )

    def clear_exempt(
        self,
        guild_id: int,
        user_id: int,
        now_iso: str,
    ) -> None:
        """個別除外登録を解除する。"""
        self.table.update_item(
            Key=self._key(guild_id, user_id),
            UpdateExpression="SET exempt = :exempt, updated_at = :now",
            ExpressionAttributeValues={
                ":exempt": {
                    "is_exempt": False,
                    "reason": None,
                    "granted_by": None,
                    "granted_at": None,
                },
                ":now": now_iso,
            },
        )

    # =============================
    #    月次在籍確認サイクル
    # =============================
    def start_review_cycle(
        self,
        guild_id: int,
        user_id: int,
        *,
        target_month: str,
        text_activity: float,
        vc_activity: float,
        score: float,
        confirm_deadline: str,
        now_iso: str,
    ) -> bool:
        """
        基準未達と判定したユーザーの在籍確認サイクルを開始する。
        current_reviewが未登録（＝進行中サイクルが無い）の場合のみ動作する。

        成功した場合はTrue、既に進行中のサイクルがある場合はFalseを返す。
        """
        review = _to_decimal(
            {
                "target_month": target_month,
                "status": "PENDING",
                "text_activity": text_activity,
                "vc_activity": vc_activity,
                "score": score,
                "confirm_deadline": confirm_deadline,
                "started_at": now_iso,
            }
        )

        try:
            self.table.update_item(
                Key=self._key(guild_id, user_id),
                UpdateExpression=(
                    "SET current_review = :review, "
                    "membership_status = :kick_pending, "
                    "review_history = if_not_exists("
                    "review_history, :empty_list), "
                    "updated_at = :now"
                ),
                ConditionExpression="attribute_not_exists(current_review)",
                ExpressionAttributeValues={
                    ":review": review,
                    ":kick_pending": "KICK_PENDING",
                    ":empty_list": [],
                    ":now": now_iso,
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

    def resolve_review_cycle(
        self,
        guild_id: int,
        user_id: int,
        *,
        decision: str,
        decided_by: Optional[int],
        reason: Optional[str],
        now_iso: str,
    ) -> bool:
        """
        進行中の在籍確認サイクルを終了する（残留 / キック等）。
        current_reviewをreview_historyへアーカイブし、current_reviewを
        削除、membership_statusを更新する。

        decisionには "RETAINED" / "KICKED" / "CANCELLED" / "ERROR" 等を渡す。
        成功した場合はTrue、進行中のサイクルが無い場合はFalseを返す。
        """
        current_item = self.get_item(guild_id, user_id)
        current_review = current_item.get("current_review")

        if not current_review:
            return False

        history_entry = {
            **current_review,
            "decision": decision,
            "decided_by": (
                str(decided_by) if decided_by is not None else None
            ),
            "reason": reason,
            "decided_at": now_iso,
        }

        next_membership_status = (
            "ACTIVE" if decision == "RETAINED" else "KICKED"
        )

        try:
            self.table.update_item(
                Key=self._key(guild_id, user_id),
                UpdateExpression=(
                    "SET review_history = list_append("
                    "if_not_exists(review_history, :empty_list), "
                    ":new_entry), "
                    "membership_status = :next_status, "
                    "updated_at = :now "
                    "REMOVE current_review"
                ),
                ConditionExpression="attribute_exists(current_review)",
                ExpressionAttributeValues={
                    ":empty_list": [],
                    ":new_entry": _to_decimal([history_entry]),
                    ":next_status": next_membership_status,
                    ":now": now_iso,
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
