# data/rainbowl/applicants_store.py

from decimal import Decimal
from typing import Any, Dict, List, Optional

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


class RainbowlStore:
    """
    rainbowl機能（入場〜面談合否判定フロー）の応募者状態を、
    zero_bot_rainbowl_applicants テーブルへ読み書きする。

    使用テーブル構造：
    - パーティションキー: guild_id (String)
    - ソートキー        : user_id  (String)

    レコードは削除しない（永続保存）。再入場のたびに、
    それまでの挑戦を application_history へアーカイブし、
    join_count をインクリメントする。
    """

    def __init__(
        self,
        table_name: str = "zero_bot_rainbowl_applicants",
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
        resp = self.table.get_item(
            Key=self._key(guild_id, user_id)
        )
        return _from_decimal(resp.get("Item", {}))

    # =============================
    #    入場時の履歴アーカイブ・カウント更新
    # =============================
    def record_join(
        self,
        guild_id: int,
        user_id: int,
        now_iso: str,
    ) -> Dict[str, Any]:
        """
        on_member_joinのたびに呼ぶ。

        既存レコードがあれば、その時点の挑戦（statusが何であれ）を
        application_history へアーカイブし、今回挑戦分のフィールドを
        リセットする。join_countを+1し、更新後のitemを返す。
        """
        key = self._key(guild_id, user_id)
        current_item = self.get_item(guild_id, user_id)

        if not current_item:
            new_item = {
                **key,
                "status": "NOT_APPLIED",
                "onboarding_step": 1,
                "join_count": 1,
                "first_joined_at": now_iso,
                "last_joined_at": now_iso,
                "application_history": [],
                "updated_at": now_iso,
            }

            self.table.put_item(
                Item=_to_decimal(new_item)
            )

            return new_item

        history_entry = {
            "attempt": (
                len(
                    current_item.get(
                        "application_history",
                        [],
                    )
                )
                + 1
            ),
            "joined_at": current_item.get(
                "last_joined_at",
                current_item.get(
                    "first_joined_at",
                    now_iso,
                ),
            ),
            "applied_at": current_item.get("applied_at"),
            "final_status": current_item.get(
                "status",
                "NOT_APPLIED",
            ),
            "verdict_reason": current_item.get(
                "verdict_reason"
            ),
            "archived_at": now_iso,
        }

        self.table.update_item(
            Key=key,
            UpdateExpression=(
                "SET application_history = list_append("
                "if_not_exists(application_history, :empty_list),"
                " :new_entry), "
                "join_count = if_not_exists(join_count, :zero)"
                " + :one, "
                "first_joined_at = if_not_exists("
                "first_joined_at, :now), "
                "last_joined_at = :now, "
                "#status = :not_applied, "
                "onboarding_step = :one, "
                "updated_at = :now "
                "REMOVE applicant_channel_id, "
                "profile_message_id, applied_at, "
                "verdict_reason"
            ),
            ExpressionAttributeNames={
                "#status": "status",
            },
            ExpressionAttributeValues={
                ":empty_list": [],
                ":new_entry": _to_decimal(
                    [history_entry]
                ),
                ":zero": 0,
                ":one": 1,
                ":now": now_iso,
                ":not_applied": "NOT_APPLIED",
            },
        )

        return self.get_item(guild_id, user_id)

    # =============================
    #    入会案内カテゴリーの段階開放
    # =============================
    def advance_onboarding_step(
        self,
        guild_id: int,
        user_id: int,
        from_step: int,
        to_step: int,
        now_iso: str,
    ) -> bool:
        """
        現在のonboarding_stepがfrom_stepと一致する場合のみ
        to_stepへ進める（二重押下・古いボタン対策）。

        成功した場合はTrue、
        既に進んでいる／未一致の場合はFalseを返す。
        """
        try:
            self.table.update_item(
                Key=self._key(guild_id, user_id),
                UpdateExpression=(
                    "SET onboarding_step = :to_step,"
                    " updated_at = :now"
                ),
                ConditionExpression=(
                    "onboarding_step = :from_step"
                ),
                ExpressionAttributeValues={
                    ":to_step": to_step,
                    ":from_step": from_step,
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

    # =============================
    #    入会申請
    # =============================
    def set_applied(
        self,
        guild_id: int,
        user_id: int,
        applicant_channel_id: int,
        now_iso: str,
    ) -> bool:
        """
        statusがNOT_APPLIED（または未登録）の場合のみ
        APPLIEDへ遷移させる。

        成功した場合はTrue、既に申請済みの場合はFalseを返す。
        """
        try:
            self.table.update_item(
                Key=self._key(guild_id, user_id),
                UpdateExpression=(
                    "SET #status = :applied, "
                    "applicant_channel_id = :channel_id, "
                    "applied_at = :now, "
                    "updated_at = :now"
                ),
                ConditionExpression=(
                    "attribute_not_exists(#status)"
                    " OR #status = :not_applied"
                ),
                ExpressionAttributeNames={
                    "#status": "status",
                },
                ExpressionAttributeValues={
                    ":applied": "APPLIED",
                    ":not_applied": "NOT_APPLIED",
                    ":channel_id": str(applicant_channel_id),
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

    # =============================
    #    面接用プロフィール提出検知
    # =============================
    def set_profile_submitted(
        self,
        guild_id: int,
        user_id: int,
        profile_message_id: int,
        now_iso: str,
    ) -> bool:
        """
        profile_message_idが未登録の場合のみ、
        今回のメッセージを面接用プロフィールとして登録する。

        成功した場合はTrue、既に登録済みの場合はFalseを返す。
        """
        try:
            self.table.update_item(
                Key=self._key(guild_id, user_id),
                UpdateExpression=(
                    "SET profile_message_id = :message_id, "
                    "#status = :submitted, "
                    "updated_at = :now"
                ),
                ConditionExpression=(
                    "attribute_not_exists(profile_message_id)"
                ),
                ExpressionAttributeNames={
                    "#status": "status",
                },
                ExpressionAttributeValues={
                    ":message_id": str(profile_message_id),
                    ":submitted": "PROFILE_SUBMITTED",
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

    # =============================
    #    運営承認（「受付」スタンプ）
    # =============================
    def set_scheduling(
        self,
        guild_id: int,
        user_id: int,
        now_iso: str,
    ) -> bool:
        """
        statusがPROFILE_SUBMITTEDの場合のみSCHEDULINGへ進める
        （多重承認防止）。

        成功した場合はTrue、既に処理済みの場合はFalseを返す。
        """
        try:
            self.table.update_item(
                Key=self._key(guild_id, user_id),
                UpdateExpression=(
                    "SET #status = :scheduling,"
                    " updated_at = :now"
                ),
                ConditionExpression=(
                    "#status = :profile_submitted"
                ),
                ExpressionAttributeNames={
                    "#status": "status",
                },
                ExpressionAttributeValues={
                    ":scheduling": "SCHEDULING",
                    ":profile_submitted": "PROFILE_SUBMITTED",
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

    # =============================
    #    合否判定
    # =============================
    def set_passed(
        self,
        guild_id: int,
        user_id: int,
        verdict_reason: str,
        now_iso: str,
    ) -> None:
        """statusをPASSEDに更新し、verdict_reasonを保存する。"""
        self.table.update_item(
            Key=self._key(guild_id, user_id),
            UpdateExpression=(
                "SET #status = :passed, "
                "verdict_reason = :reason, "
                "updated_at = :now"
            ),
            ExpressionAttributeNames={
                "#status": "status",
            },
            ExpressionAttributeValues={
                ":passed": "PASSED",
                ":reason": verdict_reason,
                ":now": now_iso,
            },
        )

    # =============================
    #    合格通知の「了解しました」ボタン
    # =============================
    def set_newcomer(
        self,
        guild_id: int,
        user_id: int,
        now_iso: str,
    ) -> bool:
        """
        statusがPASSEDの場合のみNEWCOMERへ進める
        （多重処理防止）。

        成功した場合はTrue、既に処理済み・対象外の場合はFalseを返す。
        """
        try:
            self.table.update_item(
                Key=self._key(guild_id, user_id),
                UpdateExpression=(
                    "SET #status = :newcomer,"
                    " updated_at = :now"
                ),
                ConditionExpression=(
                    "#status = :passed"
                ),
                ExpressionAttributeNames={
                    "#status": "status",
                },
                ExpressionAttributeValues={
                    ":newcomer": "NEWCOMER",
                    ":passed": "PASSED",
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

    def set_rejected(
        self,
        guild_id: int,
        user_id: int,
        verdict_reason: str,
        now_iso: str,
    ) -> bool:
        """
        statusが既にPASSEDでない場合のみREJECTEDに更新する
        （運営が先に合格処理をしていた場合の誤キック防止）。

        成功した場合はTrue、既に合格済みの場合はFalseを返す。
        """
        try:
            self.table.update_item(
                Key=self._key(guild_id, user_id),
                UpdateExpression=(
                    "SET #status = :rejected, "
                    "verdict_reason = :reason, "
                    "updated_at = :now"
                ),
                ConditionExpression=(
                    "attribute_not_exists(#status)"
                    " OR #status <> :passed"
                ),
                ExpressionAttributeNames={
                    "#status": "status",
                },
                ExpressionAttributeValues={
                    ":rejected": "REJECTED",
                    ":passed": "PASSED",
                    ":reason": verdict_reason,
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

    # =============================
    #    最新の履歴1件（入場ログEmbed用）
    # =============================
    def get_latest_history_entry(
        self,
        item: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        history: List[Dict[str, Any]] = item.get(
            "application_history",
            [],
        )

        if not history:
            return None

        return history[-1]
