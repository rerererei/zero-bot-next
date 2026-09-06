# zero_bot_voice_daily_stats

VC活動を日次（ギルド+日付単位）で記録する集計テーブル。月次活動整理（11章）の集計元、および`/zbadmin`の期間別統計コマンドで使う。

- 実装：[data/voice_daily_store.py](../../data/voice_daily_store.py)
- 書き込み元：[cogs/voice_leveling.py](../../cogs/voice_leveling.py)の`voice_snapshot_loop`（60秒ごと）
- 読み出し元：[cogs/zbadmin_commands.py](../../cogs/zbadmin_commands.py)

## キー構造

- パーティションキー：`guild_date` (String) … `"{guild_id}#{date.isoformat()}"`（例: `"123456789#2026-07-31"`）
- ソートキー：`user_id` (String)

日付はJST基準（[utils/helpers.py](../../utils/helpers.py)の`jst_now()`）。

## フィールド

| フィールド | 型 | 説明 |
|---|---|---|
| `total_min` | Number | その日のVC総滞在時間（分） |
| `solo_min` | Number | 1人でいた時間 |
| `small_group_min` | Number | 2〜3人でいた時間 |
| `mid_group_min` | Number | 4〜6人でいた時間 |
| `big_group_min` | Number | 7人以上でいた時間 |
| `muted_min` | Number | ミュート状態だった時間 |
| `updated_at` | String (ISO8601) | 最終更新日時 |

## 主な関数

- `add_daily_voice_minutes(guild_id, user_id, **minutes)`：当日分を`ADD`式で積み上げる
- `get_user_total_minutes_in_range(guild_id, user_id, date_from, date_to)`：期間内の1ユーザー合計
- `get_guild_total_minutes_in_range(guild_id, date_from, date_to)`：期間内のギルド内ユーザー別合計（`{user_id: total_min}`）

## 冪等性・注意点

- 日付ごとにパーティションが分かれるため、月次集計は日数分の`get_item`/`query`をループする実装（Range Queryではない）。日数が多い期間の集計はレイテンシに注意。
- Bot障害等で記録が欠けた日はそのまま「活動0」として扱われる。データ欠落期間の除外ロジックは別途必要（11章「判定除外」参照、未実装）。
