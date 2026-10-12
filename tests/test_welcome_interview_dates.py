from datetime import datetime, timezone

import pytest

from wanbot.welcome.interview_dates import (
    InterviewDateError,
    format_date,
    validate_dates,
)


# 日本時間では2026年10月11日16:00。
NOW = datetime(2026, 10, 11, 7, 0, tzinfo=timezone.utc)
VALUES = (
    "2026-10-11 18:00",
    "2026-10-12 20:00",
    "2026-10-13 21:30",
)


def test_dates_use_japan_time_and_keep_preference_order():
    dates = validate_dates(VALUES, now=NOW)
    assert dates[0].astimezone(timezone.utc) == datetime(
        2026, 10, 11, 9, 0, tzinfo=timezone.utc
    )
    assert format_date(dates[2]) == "2026年10月13日 21:30（日本時間）"


@pytest.mark.parametrize(
    "invalid",
    [
        "2026-10-11 15:59",
        "2026-10-11 16:00",
        "2026-02-30 18:00",
        "2026-10-12 24:00",
        "2026-10-12",
        "明日の夜",
        "2026/10/12 18:00",
        "",
    ],
)
def test_invalid_or_past_dates_are_rejected(invalid):
    with pytest.raises(InterviewDateError):
        validate_dates((invalid, VALUES[1], VALUES[2]), now=NOW)


def test_duplicate_dates_are_rejected():
    with pytest.raises(InterviewDateError):
        validate_dates((VALUES[0], VALUES[0], VALUES[2]), now=NOW)


def test_three_preferences_are_required():
    with pytest.raises(InterviewDateError):
        validate_dates(VALUES[:2], now=NOW)


def test_confirmation_rejects_date_that_has_become_past():
    validate_dates(VALUES, now=NOW)
    later = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)
    with pytest.raises(InterviewDateError):
        validate_dates(VALUES, now=later)
