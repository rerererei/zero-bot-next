# scripts/rainbowl/setup_infra.py
"""
rainbowl機能のAWSインフラをセットアップする。

- zero_bot_rainbowl_applicants テーブルの作成（未作成の場合のみ）
- zero_bot_guild_config へ rainbowl 名前空間を投入
  （既存アイテムがあれば rainbowl 属性だけを追加し、他の名前空間は保持する）

このリポジトリにはTerraform等のIaCがないため、既存の
json_data/put_*.json + boto3スクリプトという流儀に合わせている。

何度実行しても安全（べき等）：
- テーブルは存在チェックしてから作成する
- ギルド設定は rainbowl 属性だけを SET するupdate_itemを使うため、
  他の名前空間（oyanmo・leveling等）を壊さない

実行方法:
    python -m scripts.rainbowl.setup_infra
"""

import json
from pathlib import Path

import boto3
from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError


REGION = "ap-northeast-1"

APPLICANTS_TABLE_NAME = "zero_bot_rainbowl_applicants"
GUILD_CONFIG_TABLE_NAME = "zero_bot_guild_config"

GUILD_ID = "1533518300271607929"

CONFIG_JSON_PATH = Path("json_data/put_rainbowl_config.json")

_deserializer = TypeDeserializer()


def ensure_applicants_table(
    dynamodb_client,
) -> None:
    """zero_bot_rainbowl_applicants テーブルを未作成の場合のみ作成する。"""
    try:
        dynamodb_client.describe_table(
            TableName=APPLICANTS_TABLE_NAME
        )
        print(
            f"✅ テーブル {APPLICANTS_TABLE_NAME} は"
            "既に存在します（作成をスキップ）"
        )
        return

    except ClientError as exc:
        if (
            exc.response["Error"]["Code"]
            != "ResourceNotFoundException"
        ):
            raise

    print(
        f"⏳ テーブル {APPLICANTS_TABLE_NAME} を作成します..."
    )

    dynamodb_client.create_table(
        TableName=APPLICANTS_TABLE_NAME,
        AttributeDefinitions=[
            {
                "AttributeName": "guild_id",
                "AttributeType": "S",
            },
            {
                "AttributeName": "user_id",
                "AttributeType": "S",
            },
        ],
        KeySchema=[
            {
                "AttributeName": "guild_id",
                "KeyType": "HASH",
            },
            {
                "AttributeName": "user_id",
                "KeyType": "RANGE",
            },
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    waiter = dynamodb_client.get_waiter("table_exists")
    waiter.wait(TableName=APPLICANTS_TABLE_NAME)

    print(
        f"✅ テーブル {APPLICANTS_TABLE_NAME} を作成しました"
    )


def load_rainbowl_config_attribute() -> dict:
    """
    json_data/put_rainbowl_config.json（DynamoDB低レベルAttributeValue形式）
    から rainbowl 属性を読み込み、Python標準の型（str/list/dict）へ変換する。
    """
    with CONFIG_JSON_PATH.open(encoding="utf-8") as f:
        payload = json.load(f)

    raw_rainbowl_attribute = payload["Item"]["rainbowl"]

    return _deserializer.deserialize(
        raw_rainbowl_attribute
    )


def ensure_guild_config(
    dynamodb_resource,
) -> None:
    """
    zero_bot_guild_config へ rainbowl 名前空間を投入する。

    既存アイテムの有無を確認し、
    - 無ければ put_item で新規作成
    - あれば update_item で rainbowl 属性だけを追加（他の名前空間を保持）
    """
    table = dynamodb_resource.Table(
        GUILD_CONFIG_TABLE_NAME
    )

    resp = table.get_item(
        Key={"guild_id": GUILD_ID}
    )

    existing_item = resp.get("Item")
    rainbowl_config = load_rainbowl_config_attribute()

    if existing_item is None:
        print(
            f"⏳ guild_id={GUILD_ID} のアイテムが"
            "存在しないため新規作成します"
        )

        table.put_item(
            Item={
                "guild_id": GUILD_ID,
                "rainbowl": rainbowl_config,
            }
        )

        print(
            "✅ zero_bot_guild_config へ"
            " rainbowl 設定を新規投入しました"
        )
        return

    print(
        f"⏳ guild_id={GUILD_ID} のアイテムが"
        "既に存在するため、rainbowl属性のみ"
        "追加・上書きします（他の名前空間は保持）"
    )

    table.update_item(
        Key={"guild_id": GUILD_ID},
        UpdateExpression="SET rainbowl = :r",
        ExpressionAttributeValues={
            ":r": rainbowl_config,
        },
    )

    print(
        "✅ zero_bot_guild_config の"
        " rainbowl 属性を更新しました"
    )


def main() -> None:
    dynamodb_client = boto3.client(
        "dynamodb",
        region_name=REGION,
    )

    dynamodb_resource = boto3.resource(
        "dynamodb",
        region_name=REGION,
    )

    ensure_applicants_table(dynamodb_client)
    ensure_guild_config(dynamodb_resource)

    print(
        "🎉 rainbowl機能のAWSインフラセットアップが"
        "完了しました"
    )


if __name__ == "__main__":
    main()
