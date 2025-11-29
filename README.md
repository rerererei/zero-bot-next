# ZERO BOT NEXT

Discord サーバー向けの多機能 Bot。  
VC イベント検知、強制移動（おやんも）、RankCard 生成、プロフィール自動投稿など、  
サーバー運営で便利な機能を Python でまとめて実装しています。

---

## 機能一覧

### VC 関連
- VC 入退室の自動検知
- 入室した VC ごとに専用テキストチャンネルを自動作成
- 人数が 0 になったらチャンネルを自動削除
- 入室時にユーザーの最新プロフィールメッセージを埋め込み投稿
- 投稿したプロフィールメッセージは JSON で管理し、退出時に自動削除可能

### /おやんも（VC 強制移動）
- カウントダウン付きでユーザーを指定 VC へ移動
- 停止用の STOP ボタンあり
- カウント時間や機能 ON/OFF は DynamoDB のギルド設定で管理

### RankCard
- Pillow で RankCard 画像を生成
- 背景画像は S3 からロード（S3 がなくてもローカル背景で動作）
- ギルド設定に応じて背景デザインの切り替えが可能

### 設定永続化
- ギルドごとの各種設定を DynamoDB に保存
- VC 除外チャンネル、ログカテゴリ、性別ロール、RankCard 設定などを保持
- 再起動後も設定を維持

---

## 技術スタック

- Python 3.12
- discord.py 2.6+
- AWS（S3, DynamoDB, IAM, EC2）
- boto3
- Pillow
- JSON ローカルストレージ（プロフィールメッセージ管理）

---

## ディレクトリ構成

```
zero-bot-next/
├─ cogs/
│   ├─ oyanmo.py
│   ├─ voice_events.py
│   ├─ message_handler.py
│   └─ zb_commands.py
├─ utils/
│   ├─ countdown.py
│   ├─ channel_manager.py
│   ├─ helpers.py
│   ├─ rankcard_draw.py
│   └─ rankcard_s3.py
├─ data/
│   └─ guild_config_store.py
├─ assets/
│   └─ rankcard/
│       └─ default_bg.png
├─ profile_messages.json
└─ main.py
```

---

## セットアップ

### 1. インストール

```
pip install -r requirements.txt
```

### 2. .env 設定

```
TOKEN=discord-bot-token
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key
AWS_DEFAULT_REGION=ap-northeast-1

# S3 に背景画像を置く場合のみ
RANKCARD_BUCKET_NAME=your-bucket-name
```

### 3. 起動

```
python main.py
```

---

## メモ

- RankCard の背景は S3 が無くてもローカル背景で動作します。
- ギルド設定は DynamoDB に保存されるため、環境差に強いです。
- profile_messages.json は VC 入室時のプロフィール投稿の管理に使用します。
- モジュールごとに分離しているため、拡張しやすい構造です。

# 何をしたのか

- 
``` bash
sudo dnf update -y
```

``` bash 
sudo dnf install python3.9 python3.9-pip git -y
python3.9 --version
```
 