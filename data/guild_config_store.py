import boto3
from decimal import Decimal

def _to_decimal(v):
    if isinstance(v, float) or isinstance(v, int):
        return Decimal(str(v))
    if isinstance(v, dict):
        return {k: _to_decimal(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_to_decimal(x) for x in v]
    return v

def _from_decimal(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, dict):
        return {k: _from_decimal(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_from_decimal(x) for x in v]
    return v


class GuildConfigStore:
    def __init__(self, table_name="zero_bot_guild_config", region="ap-northeast-1"):
        dynamodb = boto3.resource("dynamodb", region_name=region)
        self.table = dynamodb.Table(table_name)

    def get_config(self, guild_id: int) -> dict:
        resp = self.table.get_item(Key={"guild_id": str(guild_id)})
        item = resp.get("Item")
        if not item:
            return {}

        # もし今後 "config" フィールドにまとめる設計に変えたくなったとき用の両対応
        if "config" in item:
            return _from_decimal(item["config"])

        # 今は guild_id 以外をそのまま設定として返す
        item = {k: v for k, v in item.items() if k != "guild_id"}
        return _from_decimal(item)

    def save_config(self, guild_id: int, config: dict):
        cfg = _to_decimal(config)
        self.table.put_item(
            Item={
                "guild_id": str(guild_id),
                # 今のテーブル構造に合わせて、cfg の中身をそのまま展開する
                **cfg,
            }
        )
