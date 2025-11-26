import random
from data.guild_config_store import GuildConfigStore

# ★ デフォルト文言（DBに何もなかったとき用）
DEFAULT_COMPLETION_MESSAGES = [
    "✅ いやぁー限界だったねぇ🥰おやんも🌙 {username} ",
    "✅ {username} すやぴしたの❔きゃわじゃん🥰また明日ね👋🏻",
    "✅ すーぐ寝るじゃん😪 {username} いい夢みろよ😘",
    "✅ え!? {username} どゆことぉ？寝たん？ねぇねぇ。",
]

# ★ 他のところでも使い回せるようにグローバル1個だけ
_guild_config_store = GuildConfigStore()

def get_random_success_message(guild_id: int, username: str) -> str:
    """
    ギルド設定テーブルから /おやんも のメッセージ候補を取得して、
    そこからランダムに1件返す。
    なければデフォルトリストから選ぶ。
    """
    config = _guild_config_store.get_config(guild_id) or {}
    oyanmo_cfg = config.get("oyanmo") or {}

    # DynamoDB 側に "completion_messages": ["...", "..."] を置いておける
    messages = oyanmo_cfg.get("completion_messages") or DEFAULT_COMPLETION_MESSAGES

    msg = random.choice(messages)
    return msg.replace("{username}", username)
