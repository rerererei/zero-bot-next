# scripts/rainbowl/setup_oyanmo_button_config.py
"""
rainbowl（guild_id=1533518300271607929）の oyanmo 名前空間に、
ボタン方式おやんも機能で使う2キーを追加する。

既存の oyanmo.target_voice_channel_id などには触れず、
ネストされたパス指定（SET oyanmo.xxx = :v）で
button_channel_id / log_channel_id だけを追加・更新する。

実行方法:
    python -m scripts.rainbowl.setup_oyanmo_button_config
"""

import boto3

REGION = "ap-northeast-1"
GUILD_CONFIG_TABLE_NAME = "zero_bot_guild_config"
GUILD_ID = "1533518300271607929"

# 常設おやんもボタンの設置先テキストチャンネル
BUTTON_CHANNEL_ID = "1267178022528614461"

# 「@実行者が@対象を飛ばしました」ログの投稿先テキストチャンネル
LOG_CHANNEL_ID = "1438179093333151848"


def main() -> None:
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    table = dynamodb.Table(GUILD_CONFIG_TABLE_NAME)

    table.update_item(
        Key={"guild_id": GUILD_ID},
        UpdateExpression=(
            "SET oyanmo.button_channel_id = :button_channel_id, "
            "oyanmo.log_channel_id = :log_channel_id"
        ),
        ExpressionAttributeValues={
            ":button_channel_id": BUTTON_CHANNEL_ID,
            ":log_channel_id": LOG_CHANNEL_ID,
        },
    )

    print(
        "oyanmo.button_channel_id / oyanmo.log_channel_id を"
        " zero_bot_guild_config へ追加しました。"
    )


if __name__ == "__main__":
    main()
