# zero_bot_text_daily_stats

テキスト活動を日次（ギルド+日付単位）で記録する集計テーブル。[zero_bot_voice_daily_stats](./zero_bot_voice_daily_stats.md)と対称的な設計。月次活動整理（11章）の集計元。

- 実装：[data/text_daily_store.py](../../data/text_daily_store.py)
- 書き込み元：[cogs/text_leveling.py](../../cogs/text_leveling.py)（`on_message`、既存のクールダウン・文字数フィルタを通過した投稿のみ）
- 状態：既存（2026-09-06作成、[scripts/setup_activity_tables.py](../../scripts/setup_activity_tables.py)）

## キー構造

- パーティションキー：`guild_date` (String) … `"{guild_id}#{date.isoformat()}"`（例: `"123456789#2026-07-31"`）
- ソートキー：`user_id` (String)

日付はJST基準（[utils/helpers.py](../../utils/helpers.py)の`jst_now()`）。

## フィールド

| フィールド | 型 | 説明 |
|---|---|---|
| `message_count` | Number | その日の有効投稿数（クールダウン・文字数フィルタ通過分） |
| `xp_total` | Number | その日に付与されたテキストXP合計 |
| `updated_at` | String (ISO8601) | 最終更新日時 |

## 主な関数

- `add_daily_text_activity(guild_id, user_id, *, xp, message_count)`：当日分を`ADD`式で積み上げる
- `get_user_total_in_range(guild_id, user_id, date_from, date_to)`：期間内の1ユーザー合計（`{"message_count": ..., "xp_total": ...}`）
- `get_guild_total_in_range(guild_id, date_from, date_to)`：期間内のギルド内ユーザー別合計

## 冪等性・注意点

- [zero_bot_voice_daily_stats](./zero_bot_voice_daily_stats.md)と同じ日次パーティション設計・同じ制約（月次集計は日数分ループ、データ欠落期間の除外は別途必要）。
