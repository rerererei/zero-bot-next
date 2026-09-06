# 実装スペック：入場〜面談合否判定フロー

> このファイルは実装用に情報を絞った版。検討の経緯・理由・未決定事項は `bot設計_入場から面談合否までのフロー.md` を参照。
> IDの一次情報は `bot設計_ロールとチャンネルID一覧.md`。IDを追加・変更した場合は両方のファイルを更新すること。
> 対象：既に稼働中のBot（`zero-bot-next`、`C:\ishiba_work\00_dev\discordBot\00_zero\zero-bot-next`）への追加機能として実装する。新規Botではない。
> このBotの既存の流儀（Cog／Service／Dataの3層、DynamoDBのギルド設定は`zero_bot_guild_config`に機能名前空間で保存）に合わせる。詳細は本ファイル1〜3章。

---

## 1. アーキテクチャ・ファイル構成

既存の `bdsm` 機能（`services/bdsm_config_service.py` 等）と同型に揃える。

```
services/rainbowl_config_service.py       # guild_config["rainbowl"] を dataclass 化（読み取り専用）
services/rainbowl_onboarding_service.py   # 段階開放・チャンネル生成・状態遷移のロジック本体
data/rainbowl/applicants_store.py         # 応募者ごとの状態を zero_bot_rainbowl_applicants テーブルへ読み書き
cogs/rainbowl_onboarding.py               # on_member_join／「次へ」ボタン／入会申請ボタン
cogs/rainbowl_interview.py                # 「受付」リアクション検知／プロフィール転記／/ok /ng コマンド
json_data/put_rainbowl_config.json        # zero_bot_guild_config への追加投入用（guild_id: 1533518300271607929）
```

Cogは薄く保ち、判定・DynamoDB操作は極力Service/Data層に置く（既存コードの分離方針を踏襲）。

---

## 2. DynamoDB設計

### 2-1. ギルド設定：`zero_bot_guild_config` の `rainbowl` 名前空間

既存の`bdsm`名前空間と同じ「フラットな構造＋文字列ID」で保存する。`services/rainbowl_config_service.py`で`bdsm_config_service.py`と同じパターン（`_required_positive_int`等の変換ヘルパー流用）でdataclass化する。

```json
"rainbowl": {
  "M": {
    "entrant_role_id":            { "S": "1534597519793979512" },
    "applicant_role_id":          { "S": "1534598256787460237" },
    "passed_role_id":             { "S": "1534598317252808774" },
    "member_role_id":             { "S": "1534598393370771579" },
    "staff_role_id":              { "S": "1534598450589732936" },

    "onboarding_category_id":     { "S": "1533523929602199571" },
    "interview_category_id":      { "S": "1533749814334849154" },
    "review_category_id":         { "S": "1533524366892077137" },

    "onboarding_channel_ids": {
      "L": [
        { "S": "1533524814822903928" },
        { "S": "1533641922294321253" },
        { "S": "1533642022764941313" },
        { "S": "1533712346436862013" },
        { "S": "1533712403882180688" },
        { "S": "1534601287843451020" },
        { "S": "1533712518009327637" }
      ]
    },

    "interview_info_channel_id":  { "S": "1533749868147904642" },
    "interview_voice_channel_id": { "S": "1533712921635328090" },

    "review_profiles_channel_id": { "S": "1534949844794474579" },
    "review_notes_channel_id":    { "S": "1534949294442811622" },
    "review_results_channel_id":  { "S": "1534951614429790353" },
    "join_log_channel_id":        { "S": "1534968774434750645" },
    "passed_notice_channel_id":   { "S": "1534967599614398524" },

    "reception_emoji_id":         { "S": "1535212269472849971" },
    "reception_emoji_name":       { "S": "uketsuke" }
  }
}
```

`passed_notice_channel_id`（チャンネル名「合格通知」）：本人専用チャンネルを即時削除するため、合格の旨はここへ投稿する。規約・ルールカテゴリー側のチャンネル（このドキュメントのスコープ外だが、このフローから直接参照するIDとして追加）。

「受付」リアクションはカスタム絵文字 `<:uketsuke:1535212269472849971>`。付与・検知どちらも`reception_emoji_id`（+ 判定用に`reception_emoji_name`）で行う。

`join_log_channel_id`はチャンネル「入場者詳細」（`📋 審査・記録`カテゴリー）。

`onboarding_channel_ids`は配列の並び順＝`onboarding_step`（0始まりならstep-1、1始まりならそのままindex+1）として扱う。

> ⚠️ **投入時の注意**：guild_id `1533518300271607929` に既存の`zero_bot_guild_config`アイテムがある場合、素の`put-item`で丸ごと置き換えると他の名前空間（`oyanmo`や`leveling`など、もしこのギルド用に設定済みなら）を消してしまう。先に`get-item`で既存アイテムの有無を確認し、無ければ`put-item`、あれば`update-item`で`rainbowl`属性だけを追加すること。

### 2-2. 新規テーブル：`zero_bot_rainbowl_applicants`

既存の`zero_bot_xp`テーブル（`data/backends/dynamo_store.py`）と同じキー構造に揃える。

**レコードは削除しない**（不合格・辞退・キック後も含め永続保存）。再入場時に「過去何回目か」「前回どこまで進んだか」「前回の合否・理由」を運営へ提示するための履歴として使うため。削除ポリシーは持たず、`application_history`に過去の挑戦を積み上げていく。

```
パーティションキー: guild_id (String)
ソートキー        : user_id  (String)

Item形式：
{
  "guild_id": "1533518300271607929",
  "user_id": "...",

  # 今回（現在進行中）の挑戦の状態
  "status": "PROFILE_SUBMITTED",       # 3章のステータス文字列
  "onboarding_step": 7,                 # 数値。1〜7
  "applicant_channel_id": "...",        # 生成した本人専用チャンネルのID
  "profile_message_id": "...",          # 面接用プロフィールとして扱っているメッセージID
  "applied_at": "2026-08-07T12:00:00+09:00",
  "verdict_reason": "...",              # /ok のコメント or /ng の理由。判定コマンド実行時のみ設定
  "updated_at": "...",

  # 入場・履歴に関する統計（削除しない・再入場のたびに更新）
  "join_count": 3,                      # on_member_join のたびに +1（累計の入場回数）
  "first_joined_at": "2026-01-10T09:00:00+09:00",
  "last_joined_at": "2026-08-07T12:00:00+09:00",
  "application_history": [
    {
      "attempt": 1,                     # 何回目の挑戦か（1始まり）
      "joined_at": "2026-01-10T09:00:00+09:00",
      "applied_at": "2026-01-11T10:00:00+09:00",   # 申請しなかった場合は null
      "final_status": "REJECTED",       # 今回join直前時点の status をそのままアーカイブ（NOT_APPLIED〜PASSED/REJECTED/WITHDRAWNいずれも）
      "verdict_reason": "...",          # final_status が PASSED/REJECTED の場合のみ意味を持つ
      "archived_at": "2026-08-07T12:00:00+09:00"   # 次にjoinした（＝今回のjoin）日時
    }
  ]
}
```

`data/rainbowl/applicants_store.py`は`DynamoStore`と同様に`get_item`/`update_item`（`SET`/`ADD`式）でラップする。テーブル名・リージョンは`DynamoStore`のコンストラクタ引数と同じ形（`table_name="zero_bot_rainbowl_applicants"`）で渡せるようにする。`application_history`への追加は`list_append`、`join_count`は`ADD`でインクリメントする。

### 2-3. 入場時Embed（`join_log_channel_id`＝「入場者詳細」チャンネル）

`on_member_join`のたびに、以下の項目でEmbedを1通投稿する（1章の項目1参照）。

| 項目 | 取得元 |
|---|---|
| ユーザー名（表示名） | `member.display_name` |
| ユーザー名（@handle） | `member.name`（新形式のユーザーネーム） |
| ユーザーID（数値） | `member.id` |
| アイコン | `member.display_avatar.url` |
| Discordの自己紹介文（bio） | Discord APIのユーザープロフィール取得が必要。**Botトークンでの取得可否・レート制限は実装時に要検証**（取得できない場合は欄を省略 or 「取得不可」と表示） |
| アカウント作成年月日 | `member.created_at` |
| アカウント年数 | `member.created_at`と現在時刻の差分から「n年nヶ月」を算出 |
| 何回目の入場か | `join_count`（今回加算後の値） |
| 前回どこまで審査が進んだか | `application_history`の最新要素の`final_status`（履歴がなければ「初回入場」） |
| 前回面談をしていた場合の合否・理由 | `application_history`の最新要素の`final_status`が`PASSED`/`REJECTED`の場合のみ、その`verdict_reason`もあわせて表示 |

「ユーザー名」を2種類（表示名／@handle）載せるのは、ニックネーム変更や表示名の重複だけでは本人特定が難しい場合があるため。

---

## 3. 永続View（新規パターン）

既存の`discord.ui.View`はすべて短命（`timeout`あり、再起動をまたがない前提）。「次へ」「入会申請」ボタンは数日単位で押される可能性があるため、以下が必須：

- View定義時に `timeout=None`
- `custom_id`に必要な情報を埋め込む（Pythonの変数キャプチャに頼らない。再起動後はインスタンスが作り直されるため）
  - 例：「次へ」→ `custom_id="rainbowl_onboarding_next:{step}"`
  - 例：「入会申請」→ `custom_id="rainbowl_apply"`
- `main.py`の`setup_hook`内で`bot.add_view(View(...))`を呼び、Bot起動のたびに永続Viewとして再登録する
- ボタンの`callback`側で`custom_id`をパースして対象stepを判定する（Viewインスタンスのメンバ変数は再起動後は当てにならない）

---

## 4. フロー本体（担当ファイル付き）

```
1. [on_member_join｜cogs/rainbowl_onboarding.py]
   → rainbowl_store から既存レコードを取得
     - 既存レコードがあり、かつ status が今回の挑戦の残骸（NOT_APPLIED含む何らかの値）を持つ場合、
       そのスナップショットを application_history へ追記（アーカイブ）し、今回挑戦分のフィールド
       （status/onboarding_step/applicant_channel_id/profile_message_id/applied_at/verdict_reason）をリセット
     - join_count を +1、last_joined_at を更新（初回なら first_joined_at も設定）
   → CATEGORY_ONBOARDING の権限をべき等にチェック・設定する（Discord側の事前手動設定には依存しない）
     - onboarding_category_id に対する entrant_role_id の view_channel オーバーライドが deny でなければ deny に設定
     - CH_ONBOARDING[0]（ようこそ）に対する entrant_role_id の view_channel オーバーライドが allow でなければ allow に設定
     - 既に正しい状態なら何もしない（毎回の入場でDiscord APIを無駄に呼ばないため）
   → entrant_role_id を付与
   → join_log_channel_id へ Embed を投稿（内容は2-3章参照）

2. [ボタン「次へ」｜cogs/rainbowl_onboarding.py → services/rainbowl_onboarding_service.py]
   → data/rainbowl/applicants_store.py から現在の onboarding_step を取得
   → custom_id の step と一致する場合のみ処理（不一致は無視。二重押下・古いボタン対策）
   → 押下者個人へ、次チャンネルの view_channel:allow オーバーライドを追加
   → onboarding_step をインクリメントして保存

3. [ボタン「入会申請」｜cogs/rainbowl_onboarding.py → services/rainbowl_onboarding_service.py]
   → status が NOT_APPLIED（または未登録）の場合のみ有効
   → applicant_role_id へロール変更
   → interview_category_id 配下に本人専用チャンネルを生成
     - チャンネル名："{表示名}さん（{DiscordID}）"（Discordの自動変換・文字除去の実挙動は実装時に確認）
     - 閲覧権限：本人 + staff_role_id のみ
   → 生成直後、そのチャンネルへ2通投稿
     1通目：`channel_texts/面談手続き_本人専用チャンネル案内文.md`
     2通目：`channel_texts/面談手続き_面接用プロフィールテンプレート.md`（他の文章を混ぜない）
   → status = APPLIED、applicant_channel_id を保存

   ※ [日次バッチ（詳細未定・後日設計）] status が APPLIED のまま applied_at から3日経過し、
     かつ profile_message_id が未登録（＝プロフィール未提出）のユーザーを自動キックする。
     /ng と同様に status = REJECTED・verdict_reason に自動キックである旨を保存・本人専用チャンネルを即時削除する。
     バッチの実行基盤（cron等）自体は本フロー専用ではなく、Bot全体の日次バッチとしてまとめて別途設計する。

4. [on_message｜cogs/rainbowl_interview.py]
   → メッセージのチャンネルIDが、送信者本人の applicant_channel_id と一致するかを rainbowl_store で確認
   → 一致し、かつ profile_message_id が未登録の場合のみ「これが面接用プロフィール」として扱う
     - 「受付」スタンプ（`<:uketsuke:1535212269472849971>`）をリアクション
     - profile_message_id を保存、status = PROFILE_SUBMITTED
   → 既に profile_message_id がある場合は通常のやり取りとして無視（リアクションしない）

5. [on_raw_reaction_add｜cogs/rainbowl_interview.py]
   → 対象メッセージIDが profile_message_id と一致するかを確認
   → リアクション実行者が staff_role_id を持つかを確認（本人が押しても無効）
   → status が PROFILE_SUBMITTED の場合のみ処理（多重承認防止）
   → review_profiles_channel_id へ「{本人メンション} + プロフィール全文」を転記
   → status = SCHEDULING

   不備がある場合：運営が本人専用チャンネルで直接やり取り（完全手動、Bot処理なし）。status は PROFILE_SUBMITTED のまま変更しない。
   本人が投稿を編集し、運営が改めて内容を確認して問題なければ、通常どおり同じ「受付」スタンプを押して承認する
   （NEEDS_FOLLOWUP ステータスは使わない。ステータス遷移としては何も特別なことをしない）

6. [人間の作業]
   運営が本人専用チャンネルで日程調整。（任意）review_notes_channel_id へ手書きメモを投稿（Bot処理なし）

7. [人間の作業]
   面談実施（interview_voice_channel_id または本人専用チャンネルのテキスト）
   → 任意のタイミングで status = INTERVIEW_DONE に更新

8. [/ok /ng｜cogs/rainbowl_interview.py]
   → 実行チャンネルが実行対象ユーザーの applicant_channel_id と一致するか検証
   → 実行者が staff_role_id を持つか検証（app_commands.checks.has_role(staff_role_id)等）。持たない場合は実行不可
   → 検証OKならモーダルを表示（コマンド実行への応答として即表示。DBの更新やロール変更はまだ行わない）
     - /ok  → コメント入力欄（任意）
     - /ng  → 理由入力欄（必須。空では送信不可）
   → モーダルの「送信」ボタン押下（`on_submit`）をトリガーに、以下の分岐処理を実行する

   /ng（不合格・モーダル送信時）：
   → 本人への通知は行わない（本人専用チャンネルへの投稿なし。無連絡でのキックとなる旨は面談時に運営が口頭説明する運用のため、Bot側の対応は不要）
   → 対象ユーザーをキック（直前に最新statusを再取得し、既にPASSEDなら中断）
   → review_results_channel_id へ Embed で記録
     - 左線色：赤
     - 内容：ユーザー名、ユーザーID、アイコン（`author.icon_url`）、理由（モーダル入力内容）
   → rainbowl_store の verdict_reason にモーダル入力内容（理由）を保存（次回入場時のEmbedで参照するため）
   → status = REJECTED、本人専用チャンネルを即時削除（DB側に理由・経緯が残るため即時削除で問題ない）

   /ok（合格・モーダル送信時）：
   → applicant_role_id → passed_role_id
   → 規約・ルールカテゴリーを開放（`rainbowl_server_handover.md` 7章の基本フローに合流。カテゴリーIDは今回のスコープ外のため別途）
   → review_results_channel_id へ Embed で記録
     - 左線色：緑
     - 内容：ユーザー名、ユーザーID、アイコン（`author.icon_url`）、コメント（モーダル入力内容。未入力なら空欄）
   → rainbowl_store の verdict_reason にモーダル入力内容（コメント）を保存
   → passed_notice_channel_id へ合格の旨を投稿（本人メンション。DMは使わない。本人専用チャンネルは直後に削除するためここには投稿しない）
   → status = PASSED、本人専用チャンネルを即時削除（不合格側と同じタイミング。合格の通知先を分けたことで、削除前に本人が読めるかを気にする必要がなくなった）
```

---

## 5. 権限の後片付け

`applicant_role_id`を付与するタイミングで、`CH_ONBOARDING[1]`〜`[6]`に残っている本人向け個別view_channelオーバーライドを削除する（`applicant_role_id`はロール単位で入会案内カテゴリー全体を閲覧できるため不要になる）。

---

## 6. 未実装・要決定（実装前に確認）

- Discordのユーザー自己紹介文（bio）をBotトークンで取得できるかの技術検証（取得可否・レート制限・取得できない場合のフォールバック表示。実装時に優先して検証すること）
- プロフィール未提出3日自動キックを実行する日次バッチの実行基盤（cronの設置場所・DynamoDBジョブ登録方式など）。Bot全体で日次実行したい他の処理とまとめて別途設計する

### 確定事項（追記）

- 「受付」スタンプ：カスタム絵文字 `<:uketsuke:1535212269472849971>`
- 合否コマンド：`/ok`（合格）／`/ng`（不合格）。`staff_role_id`を持つユーザーのみ実行可
- `/ok` `/ng`とも実行直後にモーダルを表示し、モーダルの送信（`on_submit`）をトリガーにDB更新・ロール変更・キック・Embed投稿を行う（コマンド実行そのものでは何も確定しない）
  - `/ok`：コメント入力欄（任意）
  - `/ng`：理由入力欄（必須）
- モーダルは`discord.ui.Modal`（都度生成、`interaction.response.send_modal`）でよい。永続View（3章）とは異なり、スラッシュコマンド実行に対する一回限りの応答なので再起動をまたぐ持続登録は不要
- 合否記録Embedの左線色：合格＝緑、不合格＝赤
- 本人への通知：合格の場合のみ`passed_notice_channel_id`（合格通知チャンネル）へ結果を投稿する。不合格は本人への連絡を行わず、無連絡のままキックする（無連絡キックの可能性がある旨は、Botではなく面談時に運営が口頭で説明する運用）
- 本人専用チャンネルは合否問わず即時削除する（アーカイブは行わない）。合格の旨は本人専用チャンネルではなく`passed_notice_channel_id`（合格通知チャンネル）へ投稿することで、削除前に本人が読めるかを気にせず即時削除できるようにした
- `zero_bot_rainbowl_applicants`のレコードは**削除しない**（永続保存）。再入場のたびに前回挑戦のスナップショットを`application_history`へ積み上げ、`join_count`をインクリメントする（2-2章参照）。チャンネルを即時削除してよいのは、この履歴がDB側に残るため
- 再入場のたびに`join_log_channel_id`（「入場者詳細」チャンネル、ID `1534968774434750645`）へEmbedを投稿する（2-3章参照）
- Discordの自己紹介文（bio）取得はできれば実現したい（未確定・技術検証が前提）
- 表示名がDiscordのチャンネル名制約で変換・除去される問題は事前対策をしない。実際に運用して困ってから対応する
- プロフィール未提出のまま申請から3日経過したユーザーは自動キックする（日次バッチで判定。バッチ基盤自体は別途設計）
- 内容確認での差し戻しは完全手動（Bot処理なし）。status は PROFILE_SUBMITTED のまま変更せず、`NEEDS_FOLLOWUP` ステータスは使わない
- 再応募のクールダウン期間は設けない（履歴は`application_history`に残るが、いつでも再応募可能）
- `CATEGORY_ONBOARDING`の入場者ロール初期権限（カテゴリーdeny／ようこそだけallow）は、Discord側の事前手動設定に依存せず、Botが`on_member_join`のたびにべき等にチェック・設定する
