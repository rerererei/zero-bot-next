# scripts/rainbowl/setup_other_features_config.py
"""
rainbowl（guild_id=1533518300271607929）に、既存の他機能
（archive / logging / oyanmo / leveling / profile / rankcard / server_name）
の設定を追加する。

既存の rainbowl 名前空間には触れず、追加する名前空間だけを
update_item の SET で個別に追加する（他の名前空間を壊さない）。

調査の結果、よりどりの guild_config にあった以下2キーは
現在のコードから一切参照されていない「死んだ設定」と判明したため含めない
（要リファクタリング。いずれコード側から掃除する）。
- logging.excluded_category_ids
- voice_events 名前空間全体（text_category_idがどこからも読まれていない）

実際に使われているのは logging.voice_text_category_id
（VC入退室ログ用テキストチャンネルの作成先カテゴリー、utils/channel_manager.py）
と、profile.excluded_category_ids
（VC入退室ログ・メッセージ削除の除外カテゴリー、message_handler.py / voice_events.py）。

実行方法:
    python -m scripts.rainbowl.setup_other_features_config
"""

from decimal import Decimal

import boto3


REGION = "ap-northeast-1"
GUILD_CONFIG_TABLE_NAME = "zero_bot_guild_config"
GUILD_ID = "1533518300271607929"

ONBOARDING_CATEGORY_ID = "1533523929602199571"
INTERVIEW_CATEGORY_ID = "1533749814334849154"
ARCHIVE_CATEGORY_ID = "1535183895165673482"

EXCLUDED_CATEGORY_IDS = [
    ONBOARDING_CATEGORY_ID,
    INTERVIEW_CATEGORY_ID,
]

NAMESPACES = {
    "server_name": "rainbowl",

    "archive": {
        "category_id": ARCHIVE_CATEGORY_ID,
    },

    # voice_text_category_id は archive_manager が使うカテゴリーと同一にする
    # （よりどりも archive.category_id と同じ値だったため踏襲）
    "logging": {
        "voice_text_category_id": ARCHIVE_CATEGORY_ID,
    },

    "oyanmo": {
        "target_voice_channel_id": (
            "1535184366370689034"
        ),
        "allowed_role_ids": [],
        "default_countdown_seconds": Decimal("10"),
        "enable_countdown": True,
        "enable_stop_button": True,
    },

    "leveling": {
        "voice_xp_per_min": Decimal("0.3"),
        "text_xp_per_message": Decimal("5"),
        "pair_multiplier": Decimal("1.2"),
        "solo_multiplier": Decimal("0.8"),
        "enable_voice_xp": True,
        "enable_text_xp": True,
        "cooldown_seconds_text": Decimal("10"),
        "ignored_category_ids": EXCLUDED_CATEGORY_IDS,
        "ignored_channel_ids": [],
    },

    "profile": {
        "gender_roles": {
            "male": {
                "role_id": (
                    "1535184466518220891"
                ),
                "color": "52479",
            },
            "female": {
                "role_id": (
                    "1535184571245658212"
                ),
                "color": "16711935",
            },
        },
        "profile_source_channel_ids": [
            "1533732434087252049",
            "1533732470443475034",
        ],
        "delete_profile_on_leave": True,
        "excluded_category_ids": EXCLUDED_CATEGORY_IDS,
        "leave_message_delete_excluded_category_ids": (
            EXCLUDED_CATEGORY_IDS
        ),
    },

    "rankcard": {
        "rank_bg_key": "kirakira.png",
        "default_name_color": "#FFFFFF",
        "default_label_color": "#AAAAAA",
    },
}


def main() -> None:
    dynamodb = boto3.resource(
        "dynamodb",
        region_name=REGION,
    )

    table = dynamodb.Table(GUILD_CONFIG_TABLE_NAME)

    update_expression_parts = []
    expression_attribute_names = {}
    expression_attribute_values = {}

    for index, (key, value) in enumerate(
        NAMESPACES.items()
    ):
        placeholder_name = f"#k{index}"
        placeholder_value = f":v{index}"

        update_expression_parts.append(
            f"{placeholder_name} = {placeholder_value}"
        )
        expression_attribute_names[placeholder_name] = key
        expression_attribute_values[placeholder_value] = (
            value
        )

    table.update_item(
        Key={"guild_id": GUILD_ID},
        UpdateExpression=(
            "SET " + ", ".join(update_expression_parts)
        ),
        ExpressionAttributeNames=(
            expression_attribute_names
        ),
        ExpressionAttributeValues=(
            expression_attribute_values
        ),
    )

    print(
        "✅ 以下の名前空間を"
        " zero_bot_guild_config へ追加しました: "
        + ", ".join(NAMESPACES.keys())
    )


if __name__ == "__main__":
    main()
