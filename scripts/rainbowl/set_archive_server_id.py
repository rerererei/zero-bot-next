# scripts/rainbowl/set_archive_server_id.py
"""
rainbowl（guild_id=1533518300271607929）の guild_config に
archive.server_id（VCテキストのアーカイブ先サーバーID）だけを設定する。

setup_other_features_config.py は他の名前空間もまとめて上書きするため、
アーカイブ先だけを変えたいときはこちらを使う。

実行方法:
    python -m scripts.rainbowl.set_archive_server_id
"""

import boto3

from scripts.rainbowl.setup_other_features_config import (
    ARCHIVE_SERVER_ID,
    GUILD_CONFIG_TABLE_NAME,
    GUILD_ID,
    REGION,
)


def main() -> None:
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    table = dynamodb.Table(GUILD_CONFIG_TABLE_NAME)

    # archive 名前空間の中の server_id だけを SET する（category_id などは残す）
    table.update_item(
        Key={"guild_id": GUILD_ID},
        UpdateExpression="SET #archive.#server_id = :server_id",
        ConditionExpression="attribute_exists(#archive)",
        ExpressionAttributeNames={
            "#archive": "archive",
            "#server_id": "server_id",
        },
        ExpressionAttributeValues={":server_id": ARCHIVE_SERVER_ID},
    )

    print(f"✅ archive.server_id = {ARCHIVE_SERVER_ID} を設定しました (guild_id={GUILD_ID})")


if __name__ == "__main__":
    main()
