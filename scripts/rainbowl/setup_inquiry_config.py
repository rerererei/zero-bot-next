# scripts/rainbowl/setup_inquiry_config.py
"""
rainbowl（guild_id=1533518300271607929）の zero_bot_guild_config に、
お問い合わせ窓口用の inquiry 名前空間を追加する（他の名前空間には触れない）。

この名前空間が無いギルドではお問い合わせ窓口は動作しない
（cogs/rainbowl_inquiry.py の get_inquiry_config）。

実行方法:
    python -m scripts.rainbowl.setup_inquiry_config
"""

import boto3


REGION = "ap-northeast-1"
GUILD_CONFIG_TABLE_NAME = "zero_bot_guild_config"
GUILD_ID = "1533518300271607929"

INQUIRY = {
    # 「問い合わせる」ボタンを設置し、問い合わせスレッドを作るチャンネル
    "channel_id": "1563388573917642862",
    # 問い合わせスレッドでメンションするロール（どちらもAdministrator権限あり）
    "notify_role_ids": [
        "1533518611493290096",  # 管理者
        "1546550998904869088",  # Supporter
    ],
}


def main() -> None:
    table = boto3.resource(
        "dynamodb",
        region_name=REGION,
    ).Table(GUILD_CONFIG_TABLE_NAME)

    table.update_item(
        Key={"guild_id": GUILD_ID},
        UpdateExpression="SET inquiry = :value",
        ExpressionAttributeValues={":value": INQUIRY},
        ConditionExpression="attribute_exists(guild_id)",
    )

    print(f"✅ inquiry を投入しました guild_id={GUILD_ID}")


if __name__ == "__main__":
    main()
