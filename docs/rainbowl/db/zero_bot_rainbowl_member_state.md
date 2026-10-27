# zero_bot_rainbowl_member_state

入会後メンバーの継続的な状態（在籍確認・活動休止・個別除外・入会後プロフィール）を、guild_id+user_idで1件だけ持つテーブル。[zero_bot_rainbowl_applicants](./zero_bot_rainbowl_applicants.md)と同じ「現在の状態＋履歴配列」の設計（レコードは削除せず、履歴をアーカイブして使い回す）。

- 実装：[data/rainbowl/member_state_store.py](../../../data/rainbowl/member_state_store.py)（`MemberStateStore`クラス）
- 用途：月次活動確認（[services/rainbowl_activity_review_service.py](../../../services/rainbowl_activity_review_service.py)）、入会後プロフィール機能（未実装）
- 状態：既存（2026-09-06作成、[scripts/setup_activity_tables.py](../../../scripts/setup_activity_tables.py)）

## キー構造

- パーティションキー：`guild_id` (String)
- ソートキー：`user_id` (String)

## フィールド

| フィールド | 型 | 説明 |
|---|---|---|
| `membership_status` | String | `ACTIVE` / `KICK_PENDING` / `KICKED` |
| `pause` | Map | 活動休止申請。`is_paused`, `reason`, `requested_at`, `resume_at`（判定は`is_paused`のみ参照。登録コマンドは無く、休止者は`exempt`で登録する運用） |
| `exempt` | Map | 運営による個別除外登録。`is_exempt`, `reason`, `granted_by`, `granted_at` |
| `current_review` | Map \| 属性なし | 進行中の在籍確認サイクル（無ければ属性自体が存在しない）。`target_month`, `status`（`PENDING`/`RETAINED`/`KICKING`）, `text_activity`, `vc_activity`, `score`, `confirm_deadline`, `started_at`, `review_channel_id`, `staff_message_id`, `retained_by`, `retained_at` |
| `review_history` | List\<Map\> | 過去の在籍確認サイクルのアーカイブ（`current_review`の内容 + `decision`, `decided_by`, `reason`, `decided_at`） |
| `initial_profile` | Map | 入会後プロフィールの初回記載（一度登録されたら不変）。`content`, `message_id`, `recorded_at` |
| `current_profile` | Map | 入会後プロフィールの最新版（編集のたびに上書き）。`content`, `message_id`, `updated_at` |
| `updated_at` | String (ISO8601) | 最終更新日時 |

面接用プロフィール（[zero_bot_rainbowl_applicants](./zero_bot_rainbowl_applicants.md)の`profile_message_id`）とは別物。こちらは合格後に作る本プロフィール（16章、公開用）を指す。

## 主な関数

- `record_initial_profile(...)` / `update_current_profile(...)`：入会後プロフィールの初回登録・更新
- `set_pause(...)` / `clear_pause(...)`：活動休止申請の登録・解除
- `set_exempt(...)` / `clear_exempt(...)`：個別除外登録・解除
- `list_exempt(guild_id)`：個別除外中のレコードを全件取得（`/activity_exclude list`用）
- `start_review_cycle(...)`：在籍確認サイクルを開始（`current_review`が未登録の場合のみ）
- `resolve_review_cycle(...)`：進行中サイクルを終了し、`review_history`へアーカイブ（`decision`に`RETAINED`/`KICKED`/`CANCELLED`/`LEFT`。`KICKED`以外は`membership_status`を`ACTIVE`に戻す）
- `list_reviews_in_progress(guild_id)`：`current_review`を持つレコードを全件取得（期限処理用）
- `set_review_fields(...)`：進行中サイクルへ`review_channel_id`・`staff_message_id`等を追記（再実行時の重複作成防止）
- `mark_review_retained(...)`：「残留」ボタン。`PENDING`かつ期限前の場合のみ`RETAINED`に
- `claim_review_kick(...)`：期限キック直前に`PENDING`→`KICKING`（残留ボタンとの競合防止）

## 冪等性・注意点

- `record_initial_profile`は`attribute_not_exists(initial_profile)`条件、`start_review_cycle`は`attribute_not_exists(current_review)`条件で、それぞれ二重実行を防ぐ。
- `resolve_review_cycle`は`attribute_exists(current_review)`条件。進行中サイクルが無い状態で呼ぶとFalseを返す。
- 再入場（[zero_bot_rainbowl_applicants](./zero_bot_rainbowl_applicants.md)の`record_join`）が起きても、このテーブルの状態は自動連動しない。再合格時に`membership_status`等をリセットする処理は未実装。

## 検討中・未確定の追加カラム

以下は次のステップで検討予定（未実装）：

- `latest_activity_snapshot`（トップレベル）：基準を満たしていた月も含めた直近の集計結果。現状「前月活動量」は集計時に日次テーブルから都度計算している

2026-10-24の実装で解消したもの：`current_review.review_channel_id`・`staff_message_id`（重複作成・重複通知防止）は採用。確認期間の延長は廃止したため`extension_log`は不要。入会日は[zero_bot_rainbowl_applicants](./zero_bot_rainbowl_applicants.md)の`newcomer_at`を参照する（このテーブルへは複製しない）。
