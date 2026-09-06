# zero_bot_xp

累積XP（ボイス・テキスト）とVC統計メタ情報、RANK CARD用の個別背景設定を持つ。

- 実装：[data/backends/dynamo_store.py](../../data/backends/dynamo_store.py)（`DynamoStore`クラス）
- 呼び出し口：[data/store.py](../../data/store.py)のラッパー関数経由
- 用途：[cogs/voice_leveling.py](../../cogs/voice_leveling.py) / [cogs/text_leveling.py](../../cogs/text_leveling.py)（直接読み書き）、[cogs/zbadmin_commands.py](../../cogs/zbadmin_commands.py) / RANK CARD描画（[data/xp_router.py](../../data/xp_router.py)経由）
- rainbowlギルド（`guild_config`に`rainbowl`名前空間を持つギルド）はこのテーブルの対象外。`data/xp_router.py`が自動的にrainbowl専用の[zero_bot_rainbowl_xp](../rainbowl/db/zero_bot_rainbowl_xp.md)へ振り分ける

## キー構造

- パーティションキー：`guild_id` (String)
- ソートキー：`user_id` (String)

## フィールド

| フィールド | 型 | 説明 |
|---|---|---|
| `voice_xp` | Number | VC滞在で累積したXP |
| `text_xp` | Number | テキスト投稿で累積したXP |
| `rank_bg_key` | String | RANK CARDの個別背景キー（未設定時はギルド設定→デフォルトにフォールバック） |
| `meta` | Map | VC統計情報一式（下記） |

### `meta`の内訳

60秒ごとの`voice_snapshot_loop`（[voice_leveling.py](../../cogs/voice_leveling.py)）で積み上げる。単位はすべて「分」。

| フィールド | 型 | 説明 |
|---|---|---|
| `total_time` | Number | VC総滞在時間 |
| `solo_time` | Number | 1人（自分のみ）でいた時間 |
| `small_group_time` | Number | 2〜3人でいた時間 |
| `mid_group_time` | Number | 4〜6人でいた時間 |
| `big_group_time` | Number | 7人以上でいた時間 |
| `muted_time` | Number | ミュート状態だった時間 |
| `max_member_count` | Number | 同時在室した最大人数 |
| `hour_buckets` | List\<Number\> (24要素) | JST0時〜23時ごとの滞在時間内訳 |
| `pair_time` | Map\<user_id, Number\> | 同席した相手ごとの滞在時間（ペア分析用） |

## 冪等性・注意点

- `voice_xp`/`text_xp`は`ADD`式でインクリメントするため、呼び出し側で重複防止（クールダウン等）を行う必要がある。
- `meta`は`get`→加工→`SET`で全体を書き戻す方式（読み書きの間に競合が起きても実害は小さい想定。60秒間隔の単一ループからしか書かれないため）。
- 累積型のため、期間を区切った集計（日次・月次）には使えない。その用途は[zero_bot_voice_daily_stats](./zero_bot_voice_daily_stats.md) / [zero_bot_text_daily_stats](./zero_bot_text_daily_stats.md)を使う。
