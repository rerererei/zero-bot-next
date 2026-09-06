# scripts/setup_activity_tables.py
"""
月次活動整理・rainbowl専用XP機能で使う新規DynamoDBテーブル4つを作成する。

- zero_bot_text_daily_stats        （汎用、テキスト活動の日次集計）
- zero_bot_rainbowl_activity_review（rainbowl専用、月次バッチの処理済みガード）
- zero_bot_rainbowl_member_state   （rainbowl専用、入会後メンバーの継続状態）
- zero_bot_rainbowl_xp             （rainbowl専用、累積XP・VC統計メタ情報）

テーブル定義: docs/db/ 、docs/rainbowl/db/ を参照。

何度実行しても安全（べき等）：テーブルは存在チェックしてから作成する。
実行には管理者権限のAWS認証情報が必要（zero-bot-userの最小権限では作成不可）。

実行方法:
    python -m scripts.setup_activity_tables
"""

import boto3
from botocore.exceptions import ClientError


REGION = "ap-northeast-1"

TABLES = [
    {
        "TableName": "zero_bot_text_daily_stats",
        "AttributeDefinitions": [
            {"AttributeName": "guild_date", "AttributeType": "S"},
            {"AttributeName": "user_id", "AttributeType": "S"},
        ],
        "KeySchema": [
            {"AttributeName": "guild_date", "KeyType": "HASH"},
            {"AttributeName": "user_id", "KeyType": "RANGE"},
        ],
    },
    {
        "TableName": "zero_bot_rainbowl_activity_review",
        "AttributeDefinitions": [
            {"AttributeName": "guild_id", "AttributeType": "S"},
            {"AttributeName": "sort_key", "AttributeType": "S"},
        ],
        "KeySchema": [
            {"AttributeName": "guild_id", "KeyType": "HASH"},
            {"AttributeName": "sort_key", "KeyType": "RANGE"},
        ],
    },
    {
        "TableName": "zero_bot_rainbowl_member_state",
        "AttributeDefinitions": [
            {"AttributeName": "guild_id", "AttributeType": "S"},
            {"AttributeName": "user_id", "AttributeType": "S"},
        ],
        "KeySchema": [
            {"AttributeName": "guild_id", "KeyType": "HASH"},
            {"AttributeName": "user_id", "KeyType": "RANGE"},
        ],
    },
    {
        "TableName": "zero_bot_rainbowl_xp",
        "AttributeDefinitions": [
            {"AttributeName": "guild_id", "AttributeType": "S"},
            {"AttributeName": "user_id", "AttributeType": "S"},
        ],
        "KeySchema": [
            {"AttributeName": "guild_id", "KeyType": "HASH"},
            {"AttributeName": "user_id", "KeyType": "RANGE"},
        ],
    },
]


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

    for table_def in TABLES:
        ensure_table(dynamodb_client, table_def)

    print("🎉 新規テーブル4つのセットアップが完了しました")


if __name__ == "__main__":
    main()
