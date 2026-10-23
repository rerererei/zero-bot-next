# scripts/rainbowl/setup_activity_review_config.py
"""
rainbowl（guild_id=1533518300271607929）の zero_bot_guild_config に、
月次活動確認（在籍確認）用の activity_review 名前空間を追加する。

既存の名前空間には触れず、activity_review だけを update_item の SET で
追加・上書きする（他の名前空間を壊さない）。

この名前空間が無いギルドでは月次活動確認は実行されない
（services/rainbowl_activity_review_service.py の get_activity_review_config）。

実行方法:
    python -m scripts.rainbowl.setup_activity_review_config
"""

from decimal import Decimal

import boto3


REGION = "ap-northeast-1"
GUILD_CONFIG_TABLE_NAME = "zero_bot_guild_config"
GUILD_ID = "1533518300271607929"

ACTIVITY_REVIEW = {
    # 在籍確認中の人に付ける確認用ロール（黄色系）
    "review_role_id": "1563222899257647185",
    # 運営向けに対象者一覧（＋「残留」ボタン）を流すチャンネル
    "staff_list_channel_id": "1563222020215406652",
    # 対象者ごとの本人専用チャンネルを作るカテゴリー
    "review_category_id": "1563222164906319913",
    # 特別除外ロール（このロールを持つ人は判定対象外）。必要になったら追加する
    "exempt_role_ids": [
        "1563233440051241071",  # お手伝いさん
    ],
    # 月間の最低活動基準（rainbowl VC XP + テキストXP の合計。2〜3人のVC約3時間相当）
    "min_score": Decimal("60"),
    # 確認期間（集計日から何日後の0:00を期限にするか）
    "confirm_days": Decimal("7"),
    # 「新人」になってから何日未満の人を判定対象外にするか
    "newcomer_grace_days": Decimal("14"),
    # 最初に判定する月（この月の分を翌月1日に集計する）
    "start_month": "2026-11",
}


def main() -> None:
    table = boto3.resource(
        "dynamodb",
        region_name=REGION,
    ).Table(GUILD_CONFIG_TABLE_NAME)

    table.update_item(
        Key={"guild_id": GUILD_ID},
        UpdateExpression="SET activity_review = :value",
        ExpressionAttributeValues={":value": ACTIVITY_REVIEW},
        ConditionExpression="attribute_exists(guild_id)",
    )

    print(f"✅ activity_review を投入しました guild_id={GUILD_ID}")


if __name__ == "__main__":
    main()
