# utils/interaction_cooldown.py
"""
ボタン・コマンドの連打対策用の汎用クールダウン判定。

呼び出し側が好きなキー（例：(user_id, custom_id)）で管理できるので、
特定の機能に依存しない。
"""

import time

_last_called_at: dict[tuple, float] = {}


def is_on_cooldown(key: tuple, cooldown_seconds: float) -> bool:
    """
    指定キーが直近cooldown_seconds以内に呼ばれていたらTrueを返す
    （＝連打とみなして無視すべき状態）。

    Falseを返した場合は、この呼び出し自体を「今回分」として
    内部の最終呼び出し時刻を更新する。
    """
    now = time.monotonic()
    last_called_at = _last_called_at.get(key)

    if (
        last_called_at is not None
        and (now - last_called_at) < cooldown_seconds
    ):
        return True

    _last_called_at[key] = now
    return False
