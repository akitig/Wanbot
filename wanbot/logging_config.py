"""Wanbotの一般ログ。回答本文やDM本文は記録しない。"""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import TextIO


LOGGER_NAME = "wanbot"
VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class SafeFormatter(logging.Formatter):
    """既知の秘密情報を伏せ、例外本文を通常ログに含めない。"""

    converter = time.gmtime

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets = tuple(
            sorted(
                {value for value in secrets if value},
                key=len,
                reverse=True,
            )
        )

    def format(self, record: logging.LogRecord) -> str:
        try:
            message = record.getMessage()
        except Exception:
            # フォーマット失敗時に元の引数を表示しない。
            message = "log_message_format_failed"

        timestamp = datetime.fromtimestamp(
            record.created, timezone.utc
        ).isoformat(timespec="seconds")

        guild_id = getattr(record, "guild_id", None)
        if type(guild_id) is not int or guild_id <= 0:
            guild_id = "-"

        line = (
            f"{timestamp} {record.levelname} {record.name} "
            f"guild_id={guild_id} {message}"
        )

        # 例外本文やトレースバックには外部入力が入る場合があるため、
        # 一般ログには例外の種類だけを記録する。
        if record.exc_info and record.exc_info[0]:
            line += f" error_type={record.exc_info[0].__name__}"

        for secret in self._secrets:
            line = line.replace(secret, "[REDACTED]")

        # 入力に改行が含まれても、別のログ行に見せかけられないようにする。
        return line.replace("\r", "\\r").replace("\n", "\\n")


def configure_logging(
    level: str = "INFO",
    *,
    secrets: Iterable[str] = (),
    stream: TextIO | None = None,
) -> logging.Logger:
    """WanbotとDiscordライブラリのログ出力先を設定する。"""
    normalized_level = level.upper()
    if normalized_level not in VALID_LEVELS:
        raise ValueError("Unsupported logging level")

    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(SafeFormatter(secrets))

    app_logger = logging.getLogger(LOGGER_NAME)
    for old_handler in tuple(app_logger.handlers):
        app_logger.removeHandler(old_handler)
        old_handler.close()
    app_logger.addHandler(handler)
    app_logger.setLevel(normalized_level)
    app_logger.propagate = False

    # Discordライブラリの詳細ログには通信内容が含まれ得るため、
    # アプリがDEBUGでもライブラリ側はWARNING以上に制限する。
    discord_logger = logging.getLogger("discord")
    for old_handler in tuple(discord_logger.handlers):
        discord_logger.removeHandler(old_handler)
        old_handler.close()
    discord_logger.addHandler(handler)
    discord_logger.setLevel(logging.WARNING)
    discord_logger.propagate = False

    return app_logger


def _identifier(value: str) -> bool:
    return bool(value) and value.isascii() and all(
        character.isalnum() or character == "_" for character in value
    )


def log_event(
    feature: str,
    event: str,
    *,
    guild_id: int | None = None,
    level: int = logging.INFO,
    error: BaseException | None = None,
) -> None:
    """固定の機能名・処理名を使って記録する。本文の引数は設けない。"""
    if not _identifier(feature) or not _identifier(event):
        raise ValueError("Feature and event must be fixed identifiers")
    if guild_id is not None and (type(guild_id) is not int or guild_id <= 0):
        raise ValueError("Invalid guild ID")

    message = f"event={event}"
    if error is not None:
        message += f" error_type={type(error).__name__}"

    logging.getLogger(f"{LOGGER_NAME}.{feature}").log(
        level,
        message,
        extra={"guild_id": guild_id},
    )
