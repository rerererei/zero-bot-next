# data/rainbowl/private_room_store.py

from decimal import Decimal
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError


def _to_decimal(value):
    """Pythonの数値 → DynamoDB対応のDecimalに変換"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
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


class PrivateRoomStore:
    """
    rainbowl専用プライベートルーム機能のアクティブルーム状態を、
    zero_bot_rainbowl_private_rooms テーブルへ読み書きする。

    シングルテーブル設計：
    - パーティションキー: guild_id (String)
    - ソートキー        : sort_key (String)
        - ルーム本体   : "ROOM#{channel_id}"
        - 所有者ロック : "OWNER#{owner_id}"（1人1部屋をDB側で保証する）
        - 未送信ログ   : "LOGQUEUE#{log_id}"

    Discordのスノーフレークは64bitでfloat変換すると精度が壊れるため、
    ID系の値（channel_id/owner_id/category_id/message_id/招待ユーザーID等）は
    すべて文字列として保存する。
    """

    def __init__(
        self,
        table_name: str = "zero_bot_rainbowl_private_rooms",
        region: str = "ap-northeast-1",
    ):
        self.table_name = table_name
        self.dynamodb = boto3.resource("dynamodb", region_name=region)
        self.table = self.dynamodb.Table(table_name)

    # =============================
    #    内部キー生成
    # =============================
    def _room_sort_key(self, channel_id: int) -> str:
        return f"ROOM#{channel_id}"

    def _owner_sort_key(self, owner_id: int) -> str:
        return f"OWNER#{owner_id}"

    def _room_key(self, guild_id: int, channel_id: int) -> Dict[str, str]:
        return {
            "guild_id": str(guild_id),
            "sort_key": self._room_sort_key(channel_id),
        }

    def _owner_key(self, guild_id: int, owner_id: int) -> Dict[str, str]:
        return {
            "guild_id": str(guild_id),
            "sort_key": self._owner_sort_key(owner_id),
        }

    # =============================
    #    ルーム作成（1人1部屋をDB側で保証）
    # =============================
    def create_room(
        self,
        guild_id: int,
        owner_id: int,
        channel_id: int,
        room_type: str,
        destination_category_id: int,
        room_name: str,
        human_limit: Optional[int],
        bitrate: int,
        now_iso: str,
    ) -> bool:
        """
        所有者ロックとルーム本体を同時に作成する（TransactWriteItems）。

        所有者が既にロックを持っている場合、またはchannel_idが既に
        登録済みの場合は失敗しFalseを返す（連打・二重作成対策）。
        """
        room_item = {
            "guild_id": str(guild_id),
            "sort_key": self._room_sort_key(channel_id),
            "channel_id": str(channel_id),
            "owner_id": str(owner_id),
            "room_type": room_type,
            "destination_category_id": str(destination_category_id),
            "room_menu_message_id": None,
            "room_name": room_name,
            "status_text": None,
            "invited_user_ids": [],
            "pending_removal_user_ids": [],
            # 無制限はNoneではなく番兵値0で保存する（REMOVEとの区別のため）
            "human_limit": human_limit if human_limit else 0,
            "bitrate": bitrate,
            "state": "CREATING",
            "empty_since": None,
            "delete_generation": 0,
            "created_at": now_iso,
            "updated_at": now_iso,
        }

        owner_item = {
            "guild_id": str(guild_id),
            "sort_key": self._owner_sort_key(owner_id),
            "channel_id": str(channel_id),
            "created_at": now_iso,
        }

        try:
            self.dynamodb.meta.client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": _to_decimal(owner_item),
                            "ConditionExpression": (
                                "attribute_not_exists(sort_key)"
                            ),
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": _to_decimal(room_item),
                            "ConditionExpression": (
                                "attribute_not_exists(sort_key)"
                            ),
                        }
                    },
                ]
            )
            return True

        except ClientError as exc:
            if exc.response["Error"]["Code"] in (
                "TransactionCanceledException",
                "ConditionalCheckFailedException",
            ):
                return False
            raise

    def delete_room(
        self,
        guild_id: int,
        owner_id: int,
        channel_id: int,
    ) -> None:
        """ルーム本体と所有者ロックを両方削除する。"""
        try:
            self.dynamodb.meta.client.transact_write_items(
                TransactItems=[
                    {
                        "Delete": {
                            "TableName": self.table_name,
                            "Key": _to_decimal(
                                self._room_key(guild_id, channel_id)
                            ),
                        }
                    },
                    {
                        "Delete": {
                            "TableName": self.table_name,
                            "Key": _to_decimal(
                                self._owner_key(guild_id, owner_id)
                            ),
                        }
                    },
                ]
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != (
                "TransactionCanceledException"
            ):
                raise

    # =============================
    #    取得
    # =============================
    def get_room(
        self,
        guild_id: int,
        channel_id: int,
    ) -> Optional[Dict[str, Any]]:
        resp = self.table.get_item(
            Key=self._room_key(guild_id, channel_id)
        )
        item = resp.get("Item")
        return _from_decimal(item) if item else None

    def get_room_by_owner(
        self,
        guild_id: int,
        owner_id: int,
    ) -> Optional[Dict[str, Any]]:
        resp = self.table.get_item(
            Key=self._owner_key(guild_id, owner_id)
        )
        lock_item = resp.get("Item")
        if not lock_item:
            return None

        return self.get_room(
            guild_id,
            int(lock_item["channel_id"]),
        )

    def list_active_rooms(
        self,
        guild_id: int,
    ) -> List[Dict[str, Any]]:
        """このギルドの全ルームレコード（所有者ロックは含まない）を返す。"""
        from boto3.dynamodb.conditions import Key

        resp = self.table.query(
            KeyConditionExpression=(
                Key("guild_id").eq(str(guild_id))
                & Key("sort_key").begins_with("ROOM#")
            )
        )
        return [_from_decimal(item) for item in resp.get("Items", [])]

    # =============================
    #    汎用フィールド更新
    # =============================
    def _set_fields(
        self,
        guild_id: int,
        channel_id: int,
        fields: Dict[str, Any],
        now_iso: str,
    ) -> None:
        """
        値がNoneのフィールドはREMOVE、それ以外はSETする汎用更新。
        部屋名・ステータス・人数上限・ビットレート・状態・
        ルームメニューメッセージID等の単純な更新に使う。
        """
        set_parts = ["updated_at = :now"]
        remove_parts = []
        names: Dict[str, str] = {}
        values: Dict[str, Any] = {":now": now_iso}

        for i, (field_name, field_value) in enumerate(fields.items()):
            name_ph = f"#f{i}"
            names[name_ph] = field_name

            if field_value is None:
                remove_parts.append(name_ph)
            else:
                val_ph = f":v{i}"
                set_parts.append(f"{name_ph} = {val_ph}")
                values[val_ph] = _to_decimal(field_value)

        expr = "SET " + ", ".join(set_parts)
        if remove_parts:
            expr += " REMOVE " + ", ".join(remove_parts)

        self.table.update_item(
            Key=self._room_key(guild_id, channel_id),
            UpdateExpression=expr,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )

    def update_room_name(
        self, guild_id, channel_id, room_name: str, now_iso: str
    ) -> None:
        self._set_fields(
            guild_id, channel_id, {"room_name": room_name}, now_iso
        )

    def update_status_text(
        self,
        guild_id,
        channel_id,
        status_text: Optional[str],
        now_iso: str,
    ) -> None:
        self._set_fields(
            guild_id, channel_id, {"status_text": status_text}, now_iso
        )

    def update_human_limit(
        self,
        guild_id,
        channel_id,
        human_limit: Optional[int],
        now_iso: str,
    ) -> None:
        self._set_fields(
            guild_id,
            channel_id,
            # human_limitは「無制限」をNoneで表す想定だが、REMOVEされると
            # 区別できなくなるため、無制限は専用の番兵値0で保存する。
            {"human_limit": human_limit if human_limit else 0},
            now_iso,
        )

    def update_bitrate(
        self, guild_id, channel_id, bitrate: int, now_iso: str
    ) -> None:
        self._set_fields(
            guild_id, channel_id, {"bitrate": bitrate}, now_iso
        )

    def update_room_menu_message_id(
        self,
        guild_id,
        channel_id,
        message_id: Optional[int],
        now_iso: str,
    ) -> None:
        self._set_fields(
            guild_id,
            channel_id,
            {
                "room_menu_message_id": (
                    str(message_id) if message_id else None
                )
            },
            now_iso,
        )

    def set_state(
        self, guild_id, channel_id, state: str, now_iso: str
    ) -> None:
        self._set_fields(guild_id, channel_id, {"state": state}, now_iso)

    def set_empty_since(
        self,
        guild_id,
        channel_id,
        empty_since_iso: Optional[str],
        now_iso: str,
    ) -> int:
        """
        empty_sinceを更新し、delete_generationをインクリメントして返す。
        自動削除待機の世代管理（再入室で待機を無効化する）に使う。
        """
        resp = self.table.update_item(
            Key=self._room_key(guild_id, channel_id),
            UpdateExpression=(
                "SET empty_since = :empty_since, "
                "delete_generation = if_not_exists("
                "delete_generation, :zero) + :one, "
                "updated_at = :now"
            ),
            ExpressionAttributeValues={
                ":empty_since": empty_since_iso,
                ":zero": 0,
                ":one": 1,
                ":now": now_iso,
            },
            ReturnValues="UPDATED_NEW",
        )
        return int(resp["Attributes"]["delete_generation"])

    # =============================
    #    招待ユーザー・解除待ちユーザー
    # =============================
    def add_invited_user(
        self, guild_id, channel_id, user_id: int, now_iso: str
    ) -> Dict[str, Any]:
        room = self.get_room(guild_id, channel_id)
        invited = list(room.get("invited_user_ids") or [])

        if str(user_id) not in invited:
            invited.append(str(user_id))
            self._set_fields(
                guild_id,
                channel_id,
                {"invited_user_ids": invited},
                now_iso,
            )
            room["invited_user_ids"] = invited

        return room

    def remove_invited_user(
        self, guild_id, channel_id, user_id: int, now_iso: str
    ) -> Dict[str, Any]:
        room = self.get_room(guild_id, channel_id)
        invited = [
            uid
            for uid in (room.get("invited_user_ids") or [])
            if uid != str(user_id)
        ]
        self._set_fields(
            guild_id, channel_id, {"invited_user_ids": invited}, now_iso
        )
        room["invited_user_ids"] = invited
        return room

    def add_pending_removal(
        self, guild_id, channel_id, user_id: int, now_iso: str
    ) -> None:
        room = self.get_room(guild_id, channel_id)
        pending = list(room.get("pending_removal_user_ids") or [])

        if str(user_id) not in pending:
            pending.append(str(user_id))
            self._set_fields(
                guild_id,
                channel_id,
                {"pending_removal_user_ids": pending},
                now_iso,
            )

    def remove_pending_removal(
        self, guild_id, channel_id, user_id: int, now_iso: str
    ) -> None:
        room = self.get_room(guild_id, channel_id)
        pending = [
            uid
            for uid in (room.get("pending_removal_user_ids") or [])
            if uid != str(user_id)
        ]
        self._set_fields(
            guild_id,
            channel_id,
            {"pending_removal_user_ids": pending},
            now_iso,
        )

    # =============================
    #    未送信ログキュー（ログTC障害時）
    # =============================
    def enqueue_log(
        self, guild_id: int, log_id: str, payload: Dict[str, Any]
    ) -> None:
        self.table.put_item(
            Item=_to_decimal(
                {
                    "guild_id": str(guild_id),
                    "sort_key": f"LOGQUEUE#{log_id}",
                    "payload": payload,
                }
            )
        )

    def list_queued_logs(self, guild_id: int) -> List[Dict[str, Any]]:
        from boto3.dynamodb.conditions import Key

        resp = self.table.query(
            KeyConditionExpression=(
                Key("guild_id").eq(str(guild_id))
                & Key("sort_key").begins_with("LOGQUEUE#")
            )
        )
        return [_from_decimal(item) for item in resp.get("Items", [])]

    def delete_queued_log(self, guild_id: int, log_id: str) -> None:
        self.table.delete_item(
            Key={
                "guild_id": str(guild_id),
                "sort_key": f"LOGQUEUE#{log_id}",
            }
        )
