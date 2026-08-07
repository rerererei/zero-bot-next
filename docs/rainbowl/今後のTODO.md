# 今後のTODO・備忘録

> 忘れそうなこと・保留中のことを都度ここに追記していく。作業が完了したら該当項目にチェックを入れる（削除はしない。いつ何をやったかの記録として残す）。

---

## 保留中

- [ ] **`bdsm`機能のチャンネルID待ち**
  `male_url_channel_id` / `female_url_channel_id` / `command_log_channel_id` の3つ。チャンネル作成後にIDをもらい、`zero_bot_guild_config`の`rainbowl`ギルド（`1533518300271607929`）へ`bdsm`名前空間を追加する。
  参考実装：[scripts/rainbowl/setup_other_features_config.py](../../scripts/rainbowl/setup_other_features_config.py)と同じ要領（`NAMESPACES`へ`bdsm`を追加して再実行、または個別に`update_item`）。

- [ ] **`rainbowl_server_handover.md` 13.1章とBDSM機能有効化の関係を注記する**
  13.1章に「MBTI・BDSM診断は相性計算へ使わない」という記載があるが、これは独自相性診断ロジックの話。今回有効化する`/bdsm_check`（ユーザー同士がbdsmtest.orgの結果を持ち寄って個別に相性%を見る独立コマンド）とは別物という認識で進めている。`bdsm`のID投入時にあわせて、13.1章か`bot設計_ロールとチャンネルID一覧.md`あたりに一言注記を追加する。

- [ ] **本番サーバーでの実機動作確認（[起動後チェックリスト.md](./起動後チェックリスト.md)）**
  コードは実装済み・構文チェック済みだが、実際にDiscord上で入場〜次へ〜入会申請〜プロフィール提出〜受付〜`/ok`/`/ng`まで一通り動かした確認はまだ行っていない。

- [ ] **プロフィール未提出3日自動キックの日次バッチ**
  仕様としては確定済み（`APPLIED`のまま`applied_at`から3日経過・`profile_message_id`未登録で自動キック）だが、バッチ本体は未実装。「Bot全体で日次実行したい処理をまとめて後で設計したい」とのことで保留中。

- [ ] **表示名がDiscordのチャンネル名制約に引っかかる場合の見え方**
  本人専用チャンネル名`"{表示名}さん（{DiscordID}）"`の実際の変換結果（絵文字・記号除去、半角スペース→ハイフン等）は未検証。事前対策はせず「困ったら直す」方針で確定済みなので、実機確認時に見え方がおかしければ都度対応する。

---

## リファクタリング候補（今回の調査で判明した「死んだ設定」）

- [ ] **`logging.excluded_category_ids`**：コード上どこからも読まれていない。よりどり含む既存ギルドのconfigにも入っているが未使用。
- [ ] **`voice_events`名前空間全体（`text_category_id`）**：コード上どこからも読まれていない。
- 実際に機能しているのは `logging.voice_text_category_id`（VC入退室ログ用テキストチャンネルの作成先カテゴリー、`utils/channel_manager.py`）と `profile.excluded_category_ids`（VC入退室ログ・メッセージ削除の除外カテゴリー、`message_handler.py` / `voice_events.py`）。
- 対応方針は未定。「死んだ設定キーをコードから消す」か「意図通りに機能するようコードを直す」かは要検討。rainbowl以外の既存ギルド設定にも影響する話なので、慎重に進める。

---

## 完了したもの（記録として残す）

- [x] rainbowl機能（入場〜面談合否）のコード一式実装
- [x] `zero_bot_rainbowl_applicants`テーブル作成
- [x] `zero_bot_guild_config`へ`rainbowl`名前空間投入
- [x] `zero_bot_guild_config`へ`server_name` / `archive` / `logging`（`voice_text_category_id`のみ）/ `oyanmo` / `leveling` / `profile` / `rankcard`を投入
- [x] **本番IAMユーザー`zero-bot-user`に`zero_bot_rainbowl_applicants`テーブルへのアクセス権限を追加**（2026-08-07）
  実機テストで「入場してもロールが付与されない」となり、EC2の`journalctl`で調査した結果、`AccessDeniedException`（`zero-bot-user`にこの新規テーブルへの権限が無かった）と判明。`dynamoDB_zerobot`インラインポリシーのResourceに`zero_bot_rainbowl_applicants`（＋`/*`）を追加して解消。
  **教訓**：新しいDynamoDBテーブルを作るときは、テーブル作成（`scripts/rainbowl/setup_infra.py`等）だけでなく、**本番で実際にBotが使うIAMユーザー（`zero-bot-user`）のポリシーにテーブルARNを追加する作業も忘れずセットで行うこと**。今回はテーブル作成を自分の管理者権限で行ったため、この権限不足に気づくのが実機テストまで遅れた。

- [x] **Botのロール階層（並び順）を「入場者」ロールより上に修正**（2026-08-07）
  IAM権限を直した後も「ロールが付与されない」が再発。原因はDiscordのロール階層で、Bot自身のロールが「入場者」ロールより下に位置していたため。Botに管理者権限（Administrator）を付与していても、**他人へのロール付与・剥奪だけはロールの並び順が別途必要**（権限チェックとは別のDiscord仕様）。並び順を修正して解消。
  **教訓**：「管理者権限を付与した」＝「ロール操作ができる」ではない。ロール付与系の機能を検証するときは、権限だけでなくロール階層（Bot自身のロールを操作対象より上に置く）も忘れずセットで確認すること。

- [x] **EC2の`zerobot.service`にPYTHONUNBUFFERED=1を追加**（2026-08-07）
  `journalctl -f`でリアルタイムに`print()`ログが出ず、原因調査が難航していた。systemd経由でパイプ実行するとPythonの標準出力がバッファリングされ、`print()`が即座にjournaldへ流れない典型的な問題と判明。`/etc/systemd/system/zerobot.service`の`[Service]`に`Environment=PYTHONUNBUFFERED=1`を追加して解消。
  **教訓**：これはrainbowl固有ではなくBot全体に影響する設定。`assets/memo/手順.md`のsystemdサービスファイルの手順にも反映しておきたい（まだ未反映）。

- [x] **「受付」絵文字が`Unknown Emoji`で失敗する問題を解消**（2026-08-07）
  `reception_emoji_id`（`1403739356438728787`／`uketsukemashita`）でリアクションしようとすると`400 Bad Request (error code: 10014): Unknown Emoji`。絵文字が作り直されていたため、新しい絵文字`<:uketsuke:1535212269472849971>`に差し替え。`zero_bot_guild_config`（DynamoDB）・`json_data/put_rainbowl_config.json`・関連ドキュメントを更新。

- [x] **Discordの自己紹介文（bio）取得の検証結果：Botトークンでは不可と確定**（2026-08-07）
  実機で`403 Forbidden (error code: 20001): Bots cannot use this endpoint`を確認。`/users/{id}/profile`はBotトークンから利用不可とDiscord側が明示的に拒否している。コードは例外を出さず「取得不可」表示にフォールバックする設計通りに動作した。今後この項目を追う必要はない（bio欄は「取得不可」で運用確定）。
