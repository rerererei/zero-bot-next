# DynamoDBテーブル一覧（汎用）

ZERO-botが複数機能・複数ギルドで共用する汎用テーブルの一覧。1テーブル1mdファイルで定義を管理する。

rainbowl機能専用のテーブル（応募者状態・月次活動整理）は別管理。[docs/rainbowl/db/](../rainbowl/db/README.md)を参照。

リージョンは全テーブル共通で`ap-northeast-1`。

| テーブル名 | 用途 | PK | SK | 実装 | AWS作成状況 |
|---|---|---|---|---|---|
| [zero_bot_xp](./zero_bot_xp.md) | 累積XP・VC統計メタ情報 | guild_id | user_id | [data/backends/dynamo_store.py](../../data/backends/dynamo_store.py) | 既存 |
| [zero_bot_guild_config](./zero_bot_guild_config.md) | ギルドごとの機能設定（名前空間ごとにまとめる） | guild_id | - | [data/guild_config_store.py](../../data/guild_config_store.py) | 既存 |
| [zero_bot_voice_daily_stats](./zero_bot_voice_daily_stats.md) | VC活動の日次集計 | guild_date | user_id | [data/voice_daily_store.py](../../data/voice_daily_store.py) | 既存 |
| [zero_bot_text_daily_stats](./zero_bot_text_daily_stats.md) | テキスト活動の日次集計 | guild_date | user_id | [data/text_daily_store.py](../../data/text_daily_store.py) | 既存（2026-09-06作成） |

## XPの振り分けについて

`zero_bot_xp`はrainbowl以外のギルド用。RANK CARD描画・`/zbadmin`系コマンドなど複数ギルドを横断するコードは、テーブルを直接読まず[data/xp_router.py](../../data/xp_router.py)を経由する。ルーターが`guild_config`の`rainbowl`名前空間の有無を見て、`zero_bot_xp`と[zero_bot_rainbowl_xp](../rainbowl/db/zero_bot_rainbowl_xp.md)のどちらを使うかを自動判定する。
