# zero_bot_rainbowl_private_rooms

プライベートルーム機能（[プライベートルーム機能.md](../プライベートルーム機能.md)）のアクティブルーム状態を持つ、rainbowl専用テーブル。ルーム本体・所有者ロック・未送信ログキューを1テーブルにまとめたシングルテーブル設計。

- 実装：[data/rainbowl/private_room_store.py](../../../data/rainbowl/private_room_store.py)（`PrivateRoomStore`クラス）
- 用途：[cogs/rainbowl_private_rooms.py](../../../cogs/rainbowl_private_rooms.py) / [services/private_room_service.py](../../../services/private_room_service.py)
- 状態：新規（[scripts/setup_private_room_table.py](../../../scripts/setup_private_room_table.py)で作成）

## キー構造

- パーティションキー：`guild_id` (String)
- ソートキー：`sort_key` (String)。3種類のレコードを同居させる。
  - `ROOM#{channel_id}` … ルーム本体
  - `OWNER#{owner_id}` … 所有者ロック（1人1部屋をDB側で保証する）
  - `LOGQUEUE#{log_id}` … 操作ログTCへ送信できなかった未送信ログ

Discordのスノーフレークは64bitで、Decimal→float変換すると精度が壊れるため、ID系の値（`channel_id`/`owner_id`/`destination_category_id`/`room_menu_message_id`/招待ユーザーIDなど）はすべて文字列で保存する。

## ルーム本体（`ROOM#{channel_id}`）のフィールド

| フィールド | 型 | 説明 |
|---|---|---|
| `channel_id` | String | VCのチャンネルID |
| `owner_id` | String | 作成者（オーナー）のユーザーID |
| `room_type` | String | `"PRIVATE_MEETING"`（プライベート会議）/ `"PRIVATE_SOLO"`（プライベート個室・会議の1対1版）。権限モデル・削除・自動修復等の挙動は両者で共通で、作成フローのみ異なる |
| `destination_category_id` | String | 本来の作成先カテゴリID |
| `room_menu_message_id` | String \| 属性なし | VCインチャに投稿したルームメニューのメッセージID |
| `room_name` | String | 部屋名 |
| `status_text` | String \| 属性なし | ステータス文言（未設定時は属性なし） |
| `invited_user_ids` | List\<String\> | 招待済みユーザーIDのリスト |
| `pending_removal_user_ids` | List\<String\> | 招待解除待ち（VC接続中のため権限即時削除できない）ユーザーIDのリスト |
| `human_limit` | Number | 人数上限。`0`は無制限を表す番兵値 |
| `bitrate` | Number | ビットレート（bps） |
| `state` | String | `CREATING` / `ACTIVE` / `EMPTY_WAIT` / `DELETING` / `CLEANUP_REQUIRED` / `CATEGORY_MISSING` |
| `empty_since` | String (ISO8601) \| 属性なし | 人間0人になった時刻（自動削除の起点） |
| `delete_generation` | Number | `empty_since`更新のたびに+1する世代カウンタ。自動削除タスクは自分が観測した世代と現在の世代が一致する場合のみ削除する（再入室による待機無効化を確実にするため） |
| `created_at` / `updated_at` | String (ISO8601) | 作成・更新日時 |

## 所有者ロック（`OWNER#{owner_id}`）のフィールド

| フィールド | 型 | 説明 |
|---|---|---|
| `channel_id` | String | 所有中のルームのチャンネルID |
| `created_at` | String (ISO8601) | 作成日時 |

ルーム作成時は`TransactWriteItems`で所有者ロックとルーム本体を同時に`Put`する（両方とも`attribute_not_exists(sort_key)`条件）。削除時も両方を同時に`Delete`する。これにより「1人1部屋」をアプリ側のロックだけでなくDB側でも保証する（[プライベートルーム機能.md](../プライベートルーム機能.md)39章）。

## 未送信ログ（`LOGQUEUE#{log_id}`）のフィールド

| フィールド | 型 | 説明 |
|---|---|---|
| `payload` | Map | 操作ログの内容（種別・対象ユーザー・日時等） |

操作ログ用TCへの送信に失敗した場合にここへ積み、ログTC復旧後に`tasks.loop`から再送・削除する（[プライベートルーム機能.md](../プライベートルーム機能.md)44章）。

## 冪等性・注意点

- ルーム作成の二重防止は`create_room`のTransactWriteItemsが担う。所有者ロックが既に存在する場合、またはchannel_idが既に登録済みの場合は`TransactionCanceledException`となりFalseを返す。
- 招待ユーザー・解除待ちユーザーのリスト操作（`add_invited_user`等）はDynamoDBの条件付きリスト操作ではなく読み取り→Python側で加工→書き戻しで実装している。ルーム単位の操作は[services/private_room_service.py](../../../services/private_room_service.py)側の`asyncio.Lock`で直列化される前提（41章）なので、read-modify-writeでも競合しない。
- `human_limit`は「無制限」を`None`ではなく`0`で保存する（DynamoDBの`REMOVE`と「無制限」を区別するため）。読み出し側は`0`を無制限として扱う。
