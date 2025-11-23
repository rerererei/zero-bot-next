# data/backends/dynamo_store.py

from typing import Dict, Any, Optional 
import boto3
from boto3.dynamodb.conditions import Key
from decimal import Decimal

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
    """DynamoDB から読んだ値を Python の型（float/int/list/dict）に戻す"""
    if isinstance(value, Decimal):
        # 分やXPは float で扱いたいので float に戻す
        return float(value)
    if isinstance(value, list):
        return [_from_decimal(v) for v in value]
    if isinstance(value, dict):
        return {k: _from_decimal(v) for k, v in value.items()}
    return value

class DynamoStore:
    """
    JsonStore と同じインターフェースを持つ DynamoDB バックエンド。

    使用テーブル構造：
    - パーティションキー: guild_id (String)
    - ソートキー        : user_id  (String)

    Item形式：
    {
        "guild_id": "123",
        "user_id": "456",
        "voice_xp": Decimal,
        "text_xp": Decimal,
        "meta": { ... }  # 統計情報もまとめて保存
    }
    """

    def __init__(self, table_name: str, region: str = "ap-northeast-1"):
        self.table_name = table_name
        self.dynamodb = boto3.resource("dynamodb", region_name=region)
        self.table = self.dynamodb.Table(table_name)

    # =============================
    #    内部キー生成
    # =============================
    def _key(self, gid: int, uid: int) -> Dict[str, str]:
        return {
            "guild_id": str(gid),
            "user_id": str(uid)
        }

    # =============================
    #    XP 読み書き
    # =============================
    def add_voice_xp(self, gid: int, uid: int, xp: float) -> None:
        self.table.update_item(
            Key=self._key(gid, uid),
            UpdateExpression="ADD voice_xp :dxp",
            ExpressionAttributeValues={":dxp": _to_decimal(xp)}
        )

    def get_voice_xp(self, gid: int, uid: int) -> float:
        item = self._get_item(gid, uid)
        return float(item.get("voice_xp", 0.0))

    def add_text_xp(self, gid: int, uid: int, xp: float) -> None:
        self.table.update_item(
            Key=self._key(gid, uid),
            UpdateExpression="ADD text_xp :dxp",
            ExpressionAttributeValues={":dxp": _to_decimal(xp)}
        )

    def get_text_xp(self, gid: int, uid: int) -> float:
        item = self._get_item(gid, uid)
        return float(item.get("text_xp", 0.0))

    # =============================
    #    VC 統計情報（meta）
    # =============================
    def get_voice_meta(self, gid: int, uid: int) -> Dict[str, float]:
        item = self._get_item(gid, uid)
        meta = item.get("meta", {})
        # ★ Decimal 混じりを全部 Python の基本型に戻す
        return _from_decimal(meta)

    def update_voice_meta(self, gid: int, uid: int, meta: dict):
        meta_dec = _to_decimal(meta)

        self.table.update_item(
            Key={
                "guild_id": str(gid),
                "user_id": str(uid)
            },
            UpdateExpression="SET meta = :meta",
            ExpressionAttributeValues={
                ":meta": meta_dec
            }
        )

    # =============================
    #    ギルド全メンバー取得
    # =============================
    def get_guild_user_stats(self, gid: int) -> Dict[int, Dict[str, float]]:
        resp = self.table.query(
            KeyConditionExpression=Key("guild_id").eq(str(gid))
        )
        items = resp.get("Items", [])

        result: Dict[int, Dict[str, float]] = {}
        for item in items:
            uid = int(item["user_id"])
            result[uid] = {
                "voice_xp": float(item.get("voice_xp", 0.0)),
                "text_xp": float(item.get("text_xp", 0.0))
            }
        return result

    # =============================
    #    内部：1件取得
    # =============================
    def _get_item(self, gid: int, uid: int) -> Dict[str, Any]:
        resp = self.table.get_item(Key=self._key(gid, uid))
        return resp.get("Item", {})


    def _to_decimal(value):
        """Pythonの数値 → DynamoDB対応のDecimalに変換"""
        if isinstance(value, float) or isinstance(value, int):
            return Decimal(str(value))
        elif isinstance(value, dict):
            return {k: _to_decimal(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [_to_decimal(v) for v in value]
        return value
    
    # =============================
    #    RankCard 背景キー
    # =============================
    def get_rank_bg_key(self, gid: int, uid: int) -> Optional[str]:
        item = self._get_item(gid, uid)
        return item.get("rank_bg_key")
