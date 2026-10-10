import io
import logging

import pytest

from wanbot.logging_config import configure_logging, log_event


@pytest.fixture
def capture():
    # テスト終了後に、既存のロガー設定を元へ戻す。
    saved = {}
    for name in ("wanbot", "discord"):
        logger = logging.getLogger(name)
        saved[name] = (
            list(logger.handlers),
            logger.level,
            logger.propagate,
            logger.disabled,
        )
        logger.handlers.clear()
        logger.disabled = False

    stream = io.StringIO()
    configure_logging(
        "INFO",
        secrets=("example-secret-token",),
        stream=stream,
    )

    try:
        yield stream
    finally:
        for name, (handlers, level, propagate, disabled) in saved.items():
            logger = logging.getLogger(name)
            logger.handlers.clear()
            logger.handlers.extend(handlers)
            logger.setLevel(level)
            logger.propagate = propagate
            logger.disabled = disabled


def test_event_contains_feature_and_guild(capture):
    log_event("bump", "success_detected", guild_id=200)
    text = capture.getvalue()

    assert "wanbot.bump" in text
    assert "guild_id=200" in text
    assert "event=success_detected" in text


def test_token_is_redacted(capture):
    logging.getLogger("wanbot.test").warning(
        "credential=%s", "example-secret-token"
    )
    text = capture.getvalue()

    assert "example-secret-token" not in text
    assert "[REDACTED]" in text


def test_exception_body_is_not_logged(capture):
    try:
        raise RuntimeError("private-answer-body")
    except RuntimeError:
        logging.getLogger("wanbot.test").exception("request_failed")

    text = capture.getvalue()
    assert "RuntimeError" in text
    assert "private-answer-body" not in text
    assert "Traceback" not in text


def test_event_error_does_not_include_exception_message(capture):
    log_event(
        "welcome",
        "create_failed",
        guild_id=200,
        level=logging.ERROR,
        error=ValueError("private-message"),
    )
    text = capture.getvalue()

    assert "ValueError" in text
    assert "private-message" not in text


def test_newlines_are_escaped(capture):
    logging.getLogger("wanbot.test").info("first\nsecond")

    assert "first\\nsecond" in capture.getvalue()
    assert len(capture.getvalue().splitlines()) == 1


def test_reconfiguration_does_not_duplicate_output(capture):
    configure_logging("INFO", stream=capture)
    configure_logging("INFO", stream=capture)
    log_event("lifecycle", "startup")

    assert capture.getvalue().count("event=startup") == 1


def test_discord_debug_logging_is_disabled(capture):
    logging.getLogger("discord.http").debug("private-http-body")
    assert "private-http-body" not in capture.getvalue()


def test_invalid_event_name_is_rejected(capture):
    with pytest.raises(ValueError):
        log_event("welcome", "arbitrary message text")


def test_invalid_level_is_rejected(capture):
    with pytest.raises(ValueError):
        configure_logging("UNKNOWN", stream=capture)
