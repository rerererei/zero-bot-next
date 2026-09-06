# rainbowl専用DynamoDBテーブル一覧

rainbowl機能（入場〜面談合否フロー・月次活動整理・XP/レベリング）専用のテーブル。他ギルド・他機能と共用しない前提で、テーブル名も`zero_bot_rainbowl_`プレフィックスで統一している。

ボット全体で共用する汎用テーブル（他ギルド向けのXP、ギルド設定、日次活動集計）は[docs/db/](../../db/README.md)を参照。`zero_bot_xp`はrainbowlギルドには使わず、下表の`zero_bot_rainbowl_xp`を使う。

リージョンは共通で`ap-northeast-1`。

| テーブル名 | 用途 | PK | SK | 実装 | AWS作成状況 |
|---|---|---|---|---|---|
| [zero_bot_rainbowl_applicants](./zero_bot_rainbowl_applicants.md) | 入場〜面談合否フローの応募者状態 | guild_id | user_id | [data/rainbowl/applicants_store.py](../../../data/rainbowl/applicants_store.py) | 既存 |
| [zero_bot_rainbowl_activity_review](./zero_bot_rainbowl_activity_review.md) | 月次活動整理バッチの処理済みガード | guild_id | sort_key | [data/rainbowl/activity_review_store.py](../../../data/rainbowl/activity_review_store.py) | 既存（2026-09-06作成） |
| [zero_bot_rainbowl_member_state](./zero_bot_rainbowl_member_state.md) | 入会後メンバーの継続状態（在籍確認・休止・除外・入会後プロフィール） | guild_id | user_id | [data/rainbowl/member_state_store.py](../../../data/rainbowl/member_state_store.py) | 既存（2026-09-06作成） |
| [zero_bot_rainbowl_xp](./zero_bot_rainbowl_xp.md) | rainbowl専用の累積XP・VC統計メタ情報 | guild_id | user_id | [data/rainbowl/xp_store.py](../../../data/rainbowl/xp_store.py) | 既存（2026-09-06作成） |

コードも`data/rainbowl/`配下にまとめている（`data/`直下の汎用ストアとは分離）。IAM権限（`zero-bot-user`）は`dynamoDB_zerobot`ポリシーを`zero_bot_*`ワイルドカードに統合済み（[今後のTODO.md](../今後のTODO.md)参照）。
