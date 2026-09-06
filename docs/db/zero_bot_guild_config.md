# zero_bot_guild_config

ギルドごとの機能設定を、機能名前空間ごとにまとめて持つ設定テーブル。

- 実装：[data/guild_config_store.py](../../data/guild_config_store.py)（`GuildConfigStore`クラス）
- 投入スクリプト例：[scripts/rainbowl/setup_infra.py](../../scripts/rainbowl/setup_infra.py) / [scripts/rainbowl/setup_other_features_config.py](../../scripts/rainbowl/setup_other_features_config.py) / [scripts/rainbowl/setup_oyanmo_button_config.py](../../scripts/rainbowl/setup_oyanmo_button_config.py)

## キー構造

- パーティションキー：`guild_id` (String)
- ソートキーなし（ギルドごとに1アイテム）

## フィールド（名前空間）

guild_id以外の各トップレベル属性が、そのまま機能ごとの設定名前空間になる。`update_item`の`SET`で特定の名前空間だけを追加・更新でき、他の名前空間を壊さない運用。

| 名前空間 | 用途 | 主な参照元 |
|---|---|---|
| `server_name` | サーバー識別名（文字列） | 各所 |
| `archive` | アーカイブ先カテゴリーID | [cogs/archive_manager.py](../../cogs/archive_manager.py) |
| `logging` | VC入退室ログ用テキストチャンネルの作成先カテゴリー（`voice_text_category_id`） | [utils/channel_manager.py](../../utils/channel_manager.py) |
| `oyanmo` | おやんも機能の対象VC・許可ロール・カウントダウン設定 | [cogs/oyanmo.py](../../cogs/oyanmo.py) |
| `leveling` | XP付与倍率・クールダウン・除外カテゴリー/チャンネル | [cogs/voice_leveling.py](../../cogs/voice_leveling.py) / [cogs/text_leveling.py](../../cogs/text_leveling.py) |
| `profile` | 性別ロール、プロフィール転記元チャンネル、退会時プロフィール削除設定、除外カテゴリー | [cogs/message_handler.py](../../cogs/message_handler.py) / [cogs/voice_events.py](../../cogs/voice_events.py) |
| `rankcard` | RANK CARDのデフォルト背景・配色 | RANK CARD描画 |
| `rainbowl` | rainbowl機能の各種チャンネル・ロールID | [cogs/rainbowl_onboarding.py](../../cogs/rainbowl_onboarding.py) / [cogs/rainbowl_interview.py](../../cogs/rainbowl_interview.py) |
| `bdsm`（投入待ち） | `/bdsm_check`用のチャンネルID3種 | [cogs/bdsm_commands.py](../../cogs/bdsm_commands.py)（[今後のTODO.md](../rainbowl/今後のTODO.md)参照） |
| `activity_review`（未実装） | 月次活動整理の配点・基準値・除外ロールID等 | 月次バッチ本体（未実装） |

## 死んだ設定（要リファクタリング、削除は未実施）

コード上どこからも読まれていないが、既存ギルド設定には残っている。

- `logging.excluded_category_ids`
- `voice_events`名前空間全体（`text_category_id`）

対応方針は未定（[今後のTODO.md](../rainbowl/今後のTODO.md)参照）。

## 冪等性・注意点

- `save_config`は指定したguild_idのアイテムを丸ごと`put_item`するため、部分的な名前空間だけを更新したい場合は`update_item`の`SET`を個別に使う（投入スクリプト群と同じ流儀）。
