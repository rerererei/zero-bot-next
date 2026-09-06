# 今後のTODO・備忘録

> 忘れそうなこと・保留中のことを都度ここに追記していく。作業が完了したら該当項目にチェックを入れる（削除はしない。いつ何をやったかの記録として残す）。

---

## 保留中

- [ ] **`bdsm`機能のチャンネルID待ち**
  `male_url_channel_id` / `female_url_channel_id` / `command_log_channel_id` の3つ。チャンネル作成後にIDをもらい、`zero_bot_guild_config`の`rainbowl`ギルド（`1533518300271607929`）へ`bdsm`名前空間を追加する。
  参考実装：[scripts/rainbowl/setup_other_features_config.py](../../scripts/rainbowl/setup_other_features_config.py)と同じ要領（`NAMESPACES`へ`bdsm`を追加して再実行、または個別に`update_item`）。

- [ ] **`rainbowl_server_handover.md` 13.1章とBDSM機能有効化の関係を注記する**
  13.1章に「MBTI・BDSM診断は相性計算へ使わない」という記載があるが、これは独自相性診断ロジックの話。今回有効化する`/bdsm_check`（ユーザー同士がbdsmtest.orgの結果を持ち寄って個別に相性%を見る独立コマンド）とは別物という認識で進めている。`bdsm`のID投入時にあわせて、13.1章か`bot設計_ロールとチャンネルID一覧.md`あたりに一言注記を追加する。

- [ ] **「新人」ロールのDiscord側作成待ち**
  合格通知メッセージの『了解しました』ボタン（[cogs/rainbowl_interview.py](../../cogs/rainbowl_interview.py)の`AcknowledgePassedButton`）を押すと、合格ロールを外して新人ロールを付与する処理は実装済み。ただし`newcomer_role_id`をDiscord側で作成してIDを控え、`zero_bot_guild_config`の`rainbowl`名前空間へ追加するまでは`RainbowlGuildConfig`の読み込み自体が失敗し、**rainbowl機能全体（入場〜合否判定含む）が動かなくなる**（新設フィールドが必須項目のため）。ロール作成・ID投入とデプロイは必ずセットで行うこと。
  ロール作成時の注意：規約・ルールカテゴリーの閲覧権限を、合格ロールと同等以上に新人ロールへ設定すること（でないとボタン押下時に規約が見えなくなる瞬間ができる）。
  ID投入後は[json_data/put_rainbowl_config.json](../../json_data/put_rainbowl_config.json)にも追記し、DBとの記録を一致させること。

- [ ] **本番サーバーでの実機動作確認（[起動後チェックリスト.md](./起動後チェックリスト.md)）**
  コードは実装済み・構文チェック済みだが、実際にDiscord上で入場〜次へ〜入会申請〜プロフィール提出〜受付〜`/ok`/`/ng`まで一通り動かした確認はまだ行っていない。

- [ ] **プロフィール未提出3日自動キックの日次バッチ**
  仕様としては確定済み（`APPLIED`のまま`applied_at`から3日経過・`profile_message_id`未登録で自動キック）だが、バッチ本体は未実装。実行基盤の技術方針は決定済み（`rainbowl_server_handover.md`12章：cronは使わず、常駐Bot内の`tasks.loop`で日次バッチ用Cog（例：`cogs/daily_batch.py`）にまとめて実装する）。この自動キックと11章の月次活動整理を同じCogにまとめて実装する。

- [ ] **月次活動整理（11章）の集計バッチ本体は未着手**
  データ層（日次テキスト活動の記録、月次処理済みガード、メンバーごとの継続状態管理）のみ先行実装済み。集計処理そのものを書くには、まだ以下が未決定・未実装：
  - 活動量の計算式／テキストとVCの配点／月間の最低活動基準（20章に記載の未決定事項）
  - 特別除外ロールの設定場所（`guild_config`への新namespace化を想定するが未確定）

- [ ] **入会後プロフィール（`data/rainbowl/member_state_store.py`の`initial_profile`/`current_profile`）の記入・編集フローが未設計**
  データ層は用意済みだが、Botへの投稿方法・編集検知の仕組み（面接用プロフィールの`profile_message_id`方式を踏襲するか等）は別機能として別途設計する。

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

- [x] **rainbowl専用XP（`zero_bot_rainbowl_xp`）の実装と、RANK CARD・`/zbadmin`系コマンドとの連携**（2026-09-06）
  [cogs/rainbowl_voice_leveling.py](../../cogs/rainbowl_voice_leveling.py) / [cogs/rainbowl_text_leveling.py](../../cogs/rainbowl_text_leveling.py)を新設し、rainbowlギルドのXP付与を汎用の`voice_leveling.py`/`text_leveling.py`から分離（`guild_config`の`rainbowl`名前空間の有無で二重付与を防止）。`utils/rankcard_draw.py`・`cogs/zbadmin_commands.py`・`utils/helpers.py`の`_xp_for_level`は、新設した[data/xp_router.py](../../data/xp_router.py)経由でギルドに応じたテーブル（`zero_bot_xp` / `zero_bot_rainbowl_xp`）へ自動振り分けするよう変更。

- [x] **月次活動整理（11章）・XP独自化用の新規DynamoDBテーブル4つの作成、および`zero-bot-user`のIAM権限追加**（2026-09-06）
  `zero_bot_text_daily_stats` / `zero_bot_rainbowl_activity_review` / `zero_bot_rainbowl_member_state` / `zero_bot_rainbowl_xp`を[scripts/setup_activity_tables.py](../../scripts/setup_activity_tables.py)で作成（管理者権限のAWS認証情報が必要、`zero-bot-user`の最小権限では`CreateTable`不可）。IAM側は`dynamoDB_zerobot`インラインポリシーに4テーブル分のARNを個別追加しようとしたところ、**ユーザーのインラインポリシーは2048バイト上限**に達し`LimitExceededException`で失敗。既存テーブルが全て`zero_bot_`プレフィックスだったため、個別列挙をやめて`Resource`を`arn:aws:dynamodb:ap-northeast-1:472277900480:table/zero_bot_*`のワイルドカード1行に統合して解消（`zero-bot-user`の`.env`認証情報から4テーブル全てへの`DescribeTable`が通ることを確認済み）。
  **教訓**：IAMの**ユーザー**インラインポリシー（ロールではない）には2048バイトという小さいサイズ上限がある。テーブルをARNで個別列挙し続ける方式は、テーブル数が増えると遠からず上限に当たる。命名規則が統一されているなら、最初からワイルドカードでまとめておく方が長期的に安全。
