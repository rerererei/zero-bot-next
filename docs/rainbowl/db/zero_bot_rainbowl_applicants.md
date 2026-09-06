# zero_bot_rainbowl_applicants

rainbowl機能（入場〜面談合否判定フロー）の応募者状態。レコードは削除せず、再入場のたびに過去の挑戦を履歴へアーカイブして使い回す。

- 実装：[data/rainbowl/applicants_store.py](../../../data/rainbowl/applicants_store.py)（`RainbowlStore`クラス）
- 用途：[cogs/rainbowl_onboarding.py](../../../cogs/rainbowl_onboarding.py) / [cogs/rainbowl_interview.py](../../../cogs/rainbowl_interview.py)
- 設計の詳細：[docs/rainbowl/bot設計_入場から面談合否までのフロー.md](../bot設計_入場から面談合否までのフロー.md)

## キー構造

- パーティションキー：`guild_id` (String)
- ソートキー：`user_id` (String)

## フィールド

| フィールド | 型 | 説明 |
|---|---|---|
| `status` | String | `NOT_APPLIED` / `APPLIED` / `PROFILE_SUBMITTED` / `SCHEDULING` / `PASSED` / `REJECTED` |
| `onboarding_step` | Number | 入会案内カテゴリーの段階開放ステップ |
| `join_count` | Number | 再入場を含む入場回数 |
| `first_joined_at` | String (ISO8601) | 初回入場日時 |
| `last_joined_at` | String (ISO8601) | 直近の入場日時 |
| `applicant_channel_id` | String | 本人専用チャンネルID |
| `profile_message_id` | String | 面接用プロフィールのメッセージID |
| `applied_at` | String (ISO8601) | 入会申請日時（3日自動キック判定の起点） |
| `verdict_reason` | String | 合否・キック理由 |
| `application_history` | List\<Map\> | 過去の挑戦のアーカイブ（`attempt`, `joined_at`, `applied_at`, `final_status`, `verdict_reason`, `archived_at`） |
| `updated_at` | String (ISO8601) | 最終更新日時 |

## 冪等性・注意点

- ステータス遷移はすべて`ConditionExpression`による条件付き書き込み（例：`set_applied`は`NOT_APPLIED`のときのみ`APPLIED`へ、`advance_onboarding_step`は現在のステップと一致するときのみ進める）。二重押下・古いボタンからの巻き戻り操作対策。
- `set_rejected`は`status <> PASSED`の場合のみ実行可能。運営が先に合格処理をしていた場合の誤キックを防ぐ。
- 合格後（`status = PASSED`）の継続的なメンバー状態（休止申請・個別除外・在籍確認サイクル）はこのテーブルの範囲外。[zero_bot_rainbowl_member_state](./zero_bot_rainbowl_member_state.md)が扱う。
- 再入場時、[zero_bot_rainbowl_member_state](./zero_bot_rainbowl_member_state.md)側の状態（`membership_status`等）は自動連動しない。再合格時にリセットする処理を別途実装する必要がある（未実装）。
