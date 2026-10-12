import asyncio
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from wanbot.welcome.interview_batch_forms import (
    InterviewBatchModal,
    available_periods,
    available_times,
    initial_period,
)
from wanbot.welcome.interview_dates import JST, InterviewDateError
from wanbot.welcome.questionnaire_forms import QuestionnaireContext


NOW = datetime(2026, 10, 11, 10, 15, tzinfo=JST)


def test_today_times_exclude_past():
    assert available_times("2026-10-11|am", now=NOW) == (
        "2026-10-11 10:30",
        "2026-10-11 11:00",
        "2026-10-11 11:30",
    )


def test_half_day_has_24_choices():
    values = available_times("2026-10-12|pm", now=NOW)
    assert len(values) == 24
    assert values[0] == "2026-10-12 12:00"
    assert values[-1] == "2026-10-12 23:30"


def test_finished_morning_is_not_offered():
    now = NOW.replace(hour=12, minute=0)
    choices = dict(available_periods(now=now))
    assert "2026-10-11|am" not in choices.values()
    assert "2026-10-11|pm" in choices.values()


@pytest.mark.parametrize(
    "period",
    ("2026-10-10|am", "2026-10-14|pm", "2026-10-11|invalid"),
)
def test_invalid_period_is_rejected(period):
    with pytest.raises(InterviewDateError):
        available_times(period, now=NOW)


def test_initial_values_preserve_am_and_pm():
    assert initial_period("2026-10-12 00:30") == "2026-10-12|am"
    assert initial_period("2026-10-12 12:00") == "2026-10-12|pm"


@pytest.mark.parametrize(
    "periods",
    (
        None,
        ("2026-10-11|am", "2026-10-12|pm", "2026-10-13|am"),
    ),
)
def test_modal_contains_three_selects(periods):
    async def scenario():
        context = QuestionnaireContext(
            guild_id=100,
            channel_id=500,
            user_id=300,
            session_id="a" * 32,
            is_available=lambda: True,
        )
        modal = InterviewBatchModal(
            context,
            on_submit=AsyncMock(),
            initial_values=("", "", ""),
            periods=periods,
            clock=lambda: NOW,
        )
        try:
            assert len(modal.children) == 3
            assert len(modal._fields) == 3
            assert all(1 <= len(field.options) <= 25 for field in modal._fields)
            modal.to_dict()
        finally:
            modal.stop()

    asyncio.run(scenario())
