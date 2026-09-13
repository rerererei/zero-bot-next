# scripts/setup_private_room_table.py
"""
プライベートルーム機能（rainbowl専用）で使う新規DynamoDBテーブルを作成する。

- zero_bot_rainbowl_private_rooms（rainbowl専用、アクティブルーム状態・
  所有者ロック・未送信ログキューを1テーブルにまとめたシングルテーブル設計）

テーブル定義: docs/rainbowl/db/zero_bot_rainbowl_private_rooms.md を参照。

何度実行しても安全（べき等）：テーブルは存在チェックしてから作成する。
実行には管理者権限のAWS認証情報が必要（zero-bot-userの最小権限では作成不可）。

実行方法:
    python -m scripts.setup_private_room_table
"""

import boto3
from botocore.exceptions import ClientError


REGION = "ap-northeast-1"

TABLE = {
    "TableName": "zero_bot_rainbowl_private_rooms",
    "AttributeDefinitions": [
        {"AttributeName": "guild_id", "AttributeType": "S"},
        {"AttributeName": "sort_key", "AttributeType": "S"},
    ],
    "KeySchema": [
        {"AttributeName": "guild_id", "KeyType": "HASH"},
        {"AttributeName": "sort_key", "KeyType": "RANGE"},
    ],
}


def ensure_table(dynamodb_client, table_def: dict) -> None:
    table_name = table_def["TableName"]

    try:
        dynamodb_client.describe_table(TableName=table_name)
        print(f"✅ テーブル {table_name} は既に存在します（作成をスキップ）")
        return
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceNotFoundException":
            raise

    print(f"⏳ テーブル {table_name} を作成します...")

    dynamodb_client.create_table(
        TableName=table_name,
        AttributeDefinitions=table_def["AttributeDefinitions"],
        KeySchema=table_def["KeySchema"],
        BillingMode="PAY_PER_REQUEST",
    )

    waiter = dynamodb_client.get_waiter("table_exists")
    waiter.wait(TableName=table_name)

    print(f"✅ テーブル {table_name} を作成しました")


def main() -> None:
    dynamodb_client = boto3.client("dynamodb", region_name=REGION)
    ensure_table(dynamodb_client, TABLE)
    print("🎉 プライベートルーム用テーブルのセットアップが完了しました")


if __name__ == "__main__":
    main()
