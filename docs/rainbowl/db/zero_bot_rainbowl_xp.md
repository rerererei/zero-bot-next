# zero_bot_rainbowl_xp

rainbowl専用の累積XP（ボイス・テキスト）とVC統計メタ情報。汎用の[zero_bot_xp](../../db/zero_bot_xp.md)から独立してフォークしたテーブル（スキーマ・XP計算式は開始時点では同一だが、以後は個別に調整する前提）。

- 実装：[data/rainbowl/xp_store.py](../../../data/rainbowl/xp_store.py)（`data/backends/dynamo_store.py`の`DynamoStore`をrainbowl専用テーブル名で利用）
- 書き込み元：[cogs/rainbowl_voice_leveling.py](../../../cogs/rainbowl_voice_leveling.py) / [cogs/rainbowl_text_leveling.py](../../../cogs/rainbowl_text_leveling.py)
- 状態：既存（2026-09-06作成、[scripts/setup_activity_tables.py](../../../scripts/setup_activity_tables.py)）

## キー構造

- パーティションキー：`guild_id` (String)
- ソートキー：`user_id` (String)

## フィールド

[zero_bot_xp](../../db/zero_bot_xp.md)と同一のフィールド構成（`voice_xp` / `text_xp` / `rank_bg_key` / `meta`）。詳細はそちらを参照。

## rainbowl専用にした理由・振り分けロジック

- `guild_config`に`rainbowl`名前空間を持つギルドは、汎用の[cogs/voice_leveling.py](../../../cogs/voice_leveling.py) / [cogs/text_leveling.py](../../../cogs/text_leveling.py)の処理対象から除外され、代わりにこのテーブルを使うrainbowl専用Cogが処理する（二重付与防止）。
- 振り分けの判定は`guild_config.get("rainbowl")`の有無で行う（ハードコードのギルドIDではなく、[rainbowl_config_service.py](../../../services/rainbowl_config_service.py)と同じ判定方法）。
- XP計算式（`calc_voice_xp_per_minute` / `calc_text_xp`）は各Cogファイル内に個別に定義しており、汎用側とコードを共有しない。将来rainbowl側だけ配点・倍率を変えても汎用側の他ギルドに影響しない。

## RANK CARD・`/zbadmin`系コマンドとの連携

RANK CARD描画（`utils/rankcard_draw.py`）や`/zbadmin`系コマンド（`show_xp` / `rank` / `setxp` / `setlv` / `voicerank` / `textrank`）は、[data/xp_router.py](../../../data/xp_router.py)経由でこのテーブルを利用する。`guild_config`に`rainbowl`名前空間があるギルドは自動的にこちら、無いギルドは[zero_bot_xp](../../db/zero_bot_xp.md)に振り分けられる（呼び出し側はどちらのギルドかを意識しなくてよい）。

`voicerank_period` / `voice_time_period`（VC滞在時間の期間集計）は、XPではなく汎用の[zero_bot_voice_daily_stats](../../db/zero_bot_voice_daily_stats.md)を直接参照するため振り分け対象外（rainbowlも含め全ギルド共通のテーブルをそのまま使う）。
