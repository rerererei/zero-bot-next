# zero_bot_rainbowl_activity_review

月次活動整理（11章）の集計バッチが、対象月をすでに処理したかどうかを表す冪等性ガード専用テーブル。個別ユーザーの状態は持たない（それは[zero_bot_rainbowl_member_state](./zero_bot_rainbowl_member_state.md)側）。

- 実装：[data/rainbowl/activity_review_store.py](../../../data/rainbowl/activity_review_store.py)（`ActivityReviewStore`クラス）
- 用途：月次活動確認（[services/rainbowl_activity_review_service.py](../../../services/rainbowl_activity_review_service.py)）。技術方針は[docs/rainbowl/rainbowl_server_handover.md](../rainbowl_server_handover.md)12章参照
- 状態：既存（2026-09-06作成、[scripts/setup_activity_tables.py](../../../scripts/setup_activity_tables.py)）

## キー構造

- パーティションキー：`guild_id` (String)
- ソートキー：`sort_key` (String) … 次の2種類（`target_month`は`"YYYY-MM"`、例: `"2026-07"`）
  - `"BATCH#{target_month}"`：月次集計の処理済みガード
  - `"KICK_SETTING#{target_month}"`：その月の確認期限キックを実行するか（`/kick_is_enabled`）

## フィールド

| フィールド | 型 | 説明 |
|---|---|---|
| `status` | String | `IN_PROGRESS` / `DONE` |
| `started_at` | String (ISO8601) | 処理権を獲得した日時 |
| `finished_at` | String (ISO8601) | 処理完了日時（`DONE`になった時点で設定） |

`KICK_SETTING#`レコード：

| フィールド | 型 | 説明 |
|---|---|---|
| `enabled` | Boolean | `false`ならその月は期限キックを見送る。レコードが無い月は有効扱い |
| `updated_by` | String | 設定した運営のユーザーID |
| `updated_at` | String (ISO8601) | 設定日時 |

## 主な関数

- `claim_monthly_batch(guild_id, target_month, now_iso, stale_before_iso)`：対象月が未着手、または前回`IN_PROGRESS`のまま`stale_before_iso`より古く残っている（＝異常終了とみなせる）場合のみ処理権を獲得する。成功時True。
- `finish_monthly_batch(guild_id, target_month, now_iso)`：`DONE`に更新する。
- `get_monthly_batch_state(guild_id, target_month)`：現在の状態を取得する。
- `get_kick_setting(guild_id, target_month)` / `set_kick_setting(...)`：月ごとのキック実行可否の取得・設定。

## 冪等性・注意点

- `claim_monthly_batch`は`ConditionExpression`で「未存在 or 古いIN_PROGRESS」のときだけ書き込みが通る条件付き`put_item`。Bot障害で`finish_monthly_batch`まで到達せず終わった場合も、`stale_before_iso`を超えれば次回実行時に再クレームできる。
- `DONE`になったバッチは再クレームされない（条件式が`IN_PROGRESS`のみを対象にしているため）。同じ月を再処理したい場合はレコードを手動で削除する必要がある。
