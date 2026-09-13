# utils/room_text_validation.py
"""
プライベートルーム機能の部屋名・ステータス共通の入力検証。

空欄／空白のみの扱い（部屋名なら自動命名、ステータスなら解除）は
呼び出し側の責務。ここでは「文字として入力された場合」の妥当性だけを見る。
不正な入力はBot側で勝手に修正せず、RoomTextValidationErrorとして返す。
"""

import re

MAX_ROOM_TEXT_LENGTH = 80

_ZERO_WIDTH_CODEPOINTS = (
    "​"  # ZERO WIDTH SPACE
    "‌"  # ZERO WIDTH NON-JOINER
    "‍"  # ZERO WIDTH JOINER
    "⁠"  # WORD JOINER
    "﻿"  # ZERO WIDTH NO-BREAK SPACE (BOM)
)

_URL_PATTERNS = [
    re.compile(r"https?://", re.IGNORECASE),
    re.compile(r"www\.", re.IGNORECASE),
    re.compile(r"discord\.gg/", re.IGNORECASE),
    re.compile(r"discord(?:app)?\.com/invite/", re.IGNORECASE),
    # 一般的なドメイン形式（例: example.com, sub.example.co.jp）
    re.compile(
        r"\b(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}\b",
        re.IGNORECASE,
    ),
]


class RoomTextValidationError(Exception):
    """部屋名・ステータスの入力が不正な場合の例外。"""


def strip_zero_width(text: str) -> str:
    """ゼロ幅文字等を除去する。"""
    for ch in _ZERO_WIDTH_CODEPOINTS:
        text = text.replace(ch, "")
    return text


def validate_room_text(raw_text: str) -> str:
    """
    部屋名・ステータス共通の検証。ゼロ幅文字を除去したうえで検証し、
    正規化済みの文字列を返す。空文字列の扱いは呼び出し側で判定すること
    （このチェック自体は空文字列を許容する）。

    不正な場合はRoomTextValidationErrorを投げる。
    """
    text = strip_zero_width(raw_text)

    if len(text) > MAX_ROOM_TEXT_LENGTH:
        raise RoomTextValidationError(
            f"最大{MAX_ROOM_TEXT_LENGTH}文字までです。"
        )

    if "\n" in text or "\r" in text:
        raise RoomTextValidationError("改行は使用できません。")

    if "\t" in text:
        raise RoomTextValidationError("タブは使用できません。")

    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        raise RoomTextValidationError("制御文字は使用できません。")

    if "@everyone" in text or "@here" in text:
        raise RoomTextValidationError(
            "@everyone・@hereは使用できません。"
        )

    for pattern in _URL_PATTERNS:
        if pattern.search(text):
            raise RoomTextValidationError(
                "URL・招待リンクは使用できません。"
            )

    return text
