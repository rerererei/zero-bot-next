# rainbowl（恋愛・交流サーバー）入場〜面談合否機能 ドキュメント

このディレクトリは、既存の zero-bot-next に追加する新機能「rainbowlサーバーの入場〜面談合否判定フロー」の設計資料一式です。
新規Botではなく、このリポジトリで**既に稼働中のBotへの追加実装**です。

設計・検討の本体は別ディレクトリ（`C:\Users\reiis\Downloads\ZeroBot_VC仕様書_md\れいんぼーる構想\`）にあり、ここにはそのコピーを置いています。**このディレクトリの内容を更新したら、必ず元ディレクトリ側にも反映してください**（逆方向も同様）。二重管理になっている点に注意。

---

## 実装するときはまずこれを読む

**[`bot実装スペック_入場〜面談合否.md`](./bot実装スペック_入場〜面談合否.md)**

実装に必要な情報（DynamoDBのID一覧、状態機械、ファイル構成、フロー本体、永続Viewの注意点）をこの1ファイルに凝縮しています。コーディングを始めるときはここだけ読めば足ります。

---

## ファイル一覧

| ファイル | 内容 | いつ読むか |
|---|---|---|
| `bot実装スペック_入場〜面談合否.md` | **実装用の凝縮スペック（最優先）** | 実装開始時、必ず最初に読む |
| `bot設計_入場から面談合否までのフロー.md` | 同じ内容の検討経緯・理由・未決定事項つき版 | 「なぜこの設計にしたか」を確認したいとき |
| `bot設計_ロールとチャンネルID一覧.md` | ロール／カテゴリー／チャンネルのDiscord ID一次情報 | IDに疑問が出たとき、IDを追加・変更するとき |
| `rainbowl_server_handover.md` | サーバー全体のコンセプト・規約・チャンネル構成（入場〜面談合否フロー以外の範囲も含む全体像） | サーバー全体の文脈を知りたいとき |
| `channel_texts/` | Discordへそのまま貼り付けるチャンネル本文（入会案内7つ、規約3つ、面談手続き関連） | Botがメッセージを投稿する処理を書くとき、投稿文言をそのまま使う |
| `起動後チェックリスト.md` | Bot起動後、本番サーバーで動作確認する際の手順（1回だけ行う作業／エンドツーエンドの確認項目） | 実装後、初めて動かして確認するとき |
| `今後のTODO.md` | 保留中のタスク・忘れそうなことの備忘録。都度追記していく生きたメモ | 作業を再開するとき、何か思い出したいとき |

---

## 現在の状態

- ロール・カテゴリー・チャンネルはDiscord上に作成済み、IDもすべて確定済み（`bot設計_ロールとチャンネルID一覧.md`参照）
- 実コードは実装済み：`services/rainbowl_config_service.py` ＋ `services/rainbowl_onboarding_service.py` ＋ `services/rainbowl_texts.py` ＋ `data/rainbowl/applicants_store.py` ＋ `cogs/rainbowl_onboarding.py` ＋ `cogs/rainbowl_interview.py`（既存の`bdsm`機能と同型）。rainbowl専用のDynamoDBテーブル定義は[docs/rainbowl/db/](./db/README.md)にまとめてある
- AWSインフラ（`zero_bot_rainbowl_applicants`テーブル、`zero_bot_guild_config`の`rainbowl`設定）も投入済み（`scripts/rainbowl/setup_infra.py`）
- 本番サーバーでの動作確認は**これから**。手順は`起動後チェックリスト.md`を参照
- 細部の未決定事項（プロフィール未提出3日自動キックの日次バッチ基盤など）は`bot実装スペック_入場〜面談合否.md`の末尾「未実装・要決定」を参照

---

## Botに指示するときの例

```
docs/rainbowl/bot実装スペック_入場〜面談合否.md を読んで、
services/rainbowl_config_service.py を bdsm_config_service.py と同じ形式で実装して。
```
