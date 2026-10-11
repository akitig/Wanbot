"""指定したDISBOARDの投稿から、BUMP成功を判定する。"""

from __future__ import annotations

import re
from typing import Any


_ENGLISH_SUCCESS = re.compile(
    r"\bbump(?:ed)?\s+(?:done|complete|completed|success|successful)\b",
    re.IGNORECASE,
)

_JAPANESE_SUCCESS = re.compile(
    r"bump\s*(?:が|に)?\s*(?:成功|完了)"
    r"(?:しました|した|です)?(?:[!！。.\s]|$)"
    r"|表示順をアップ(?:した|しました)",
    re.IGNORECASE,
)

_FAILURE = re.compile(
    r"\b(?:failed|failure|unsuccessful)\b"
    r"|\bnot\s+(?:done|complete(?:d)?|successful)\b"
    r"|失敗"
    r"|成功していません"
    r"|完了していません",
    re.IGNORECASE,
)


def _text_fragments(message: Any) -> list[str]:
    fragments: list[str] = []

    content = getattr(message, "content", None)
    if isinstance(content, str):
        fragments.append(content)

    for embed in getattr(message, "embeds", None) or ():
        for value in (
            getattr(embed, "title", None),
            getattr(embed, "description", None),
            getattr(getattr(embed, "author", None), "name", None),
            getattr(getattr(embed, "footer", None), "text", None),
        ):
            if isinstance(value, str):
                fragments.append(value)

        for field in getattr(embed, "fields", None) or ():
            for value in (
                getattr(field, "name", None),
                getattr(field, "value", None),
            ):
                if isinstance(value, str):
                    fragments.append(value)

    return fragments


def is_bump_success_message(
    message: Any,
    *,
    guild_id: int,
    channel_id: int,
    disboard_bot_id: int,
) -> bool:
    """コマンド名だけでは成功と判定しない。"""
    if getattr(getattr(message, "guild", None), "id", None) != guild_id:
        return False

    if getattr(getattr(message, "channel", None), "id", None) != channel_id:
        return False

    author = getattr(message, "author", None)
    if getattr(author, "id", None) != disboard_bot_id:
        return False
    if getattr(author, "bot", False) is not True:
        return False

    fragments = _text_fragments(message)

    # 成功文と失敗文が混在する応答は、成功扱いにしない。
    if any(_FAILURE.search(fragment) for fragment in fragments):
        return False

    return any(
        _ENGLISH_SUCCESS.search(fragment)
        or _JAPANESE_SUCCESS.search(fragment)
        for fragment in fragments
    )
