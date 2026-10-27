# scripts/rainbowl/setup_vc_create_config.py
"""
rainbowl（guild_id=1533518300271607929）の zero_bot_guild_config の
rainbowl.private_room に、「VC作成」ボタン用の設定を追加する
（private_room内の他の項目・他の名前空間には触れない）。

- create_button_channel_id：「VC作成」ボタンを設置するチャンネル
- room_kinds：部屋種別ごとの作成先カテゴリ（キーは private_room_service.ROOM_KINDS）

未設定のままだと、/set_vc_create_button は設置を拒否し、
モーダル送信時も「作成先が設定されていない」と返す（他機能には影響しない）。

実行方法:
    python -m scripts.rainbowl.setup_vc_create_config
"""

import boto3


REGION = "ap-northeast-1"
GUILD_CONFIG_TABLE_NAME = "zero_bot_guild_config"
GUILD_ID = "1533518300271607929"

# 「VC作成」ボタンを設置するテキストチャンネル（VC作成）
CREATE_BUTTON_CHANNEL_ID = "1564657494049947718"

ROOM_KINDS = {
    "HOBBY": "1533759946414493876",  # 趣味部屋
    "WORK": "1548687034024206406",  # 作業部屋
    "FREE": "1564660566629421096",  # フリールーム
}


def main() -> None:
    table = boto3.resource(
        "dynamodb",
        region_name=REGION,
    ).Table(GUILD_CONFIG_TABLE_NAME)

    table.update_item(
        Key={"guild_id": GUILD_ID},
        UpdateExpression=(
            "SET #rb.#pr.create_button_channel_id = :channel,"
            " #rb.#pr.room_kinds = :kinds"
        ),
        ExpressionAttributeNames={
            "#rb": "rainbowl",
            "#pr": "private_room",
        },
        ExpressionAttributeValues={
            ":channel": CREATE_BUTTON_CHANNEL_ID,
            ":kinds": ROOM_KINDS,
        },
        ConditionExpression="attribute_exists(#rb.#pr)",
    )

    print(f"✅ VC作成ボタンの設定を投入しました guild_id={GUILD_ID}")


if __name__ == "__main__":
    main()
