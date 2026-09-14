# scripts/setup_server_logs_config.py
"""
サーバーログ機能（管理ログ・編集ログ・削除ログ・VCログ）の投稿先チャンネルを
zero_bot_guild_config テーブルへ登録する。

既存のguild_config（rainbowl設定等）を壊さないよう、read-modify-writeで
"server_logs" 名前空間だけを追加/更新する。

何度実行しても安全（べき等・上書き）。

実行方法:
    python -m scripts.setup_server_logs_config
"""

from data.guild_config_store import GuildConfigStore
from services import server_log_service


# rainbowlギルド（docs/rainbowl/rainbowl_server_handover.md 等で使用されているID）
RAINBOWL_GUILD_ID = 1533518300271607929

SERVER_LOGS_SETTINGS = {
    server_log_service.LOG_TYPE_ADMIN: "1548978397332373514",
    server_log_service.LOG_TYPE_EDIT: "1548978115840180297",
    server_log_service.LOG_TYPE_DELETE: "1548978256818999386",
    server_log_service.LOG_TYPE_VOICE: "1548978364147179610",
}


def main() -> None:
    store = GuildConfigStore()

    cfg = store.get_config(RAINBOWL_GUILD_ID) or {}
    cfg = {**cfg, "server_logs": SERVER_LOGS_SETTINGS}
    store.save_config(RAINBOWL_GUILD_ID, cfg)

    print(
        "✅ サーバーログ設定を登録しました"
        f" guild_id={RAINBOWL_GUILD_ID}"
        f" settings={SERVER_LOGS_SETTINGS}"
    )


if __name__ == "__main__":
    main()
