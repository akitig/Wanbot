import asyncio
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from wanbot.welcome.interview_dates import JST, InterviewDateError
from wanbot.welcome.interview_preferences_view import (
    InterviewPreferencesView,
    PreferenceModal,
    selected_datetime,
)
from wanbot.welcome.questionnaire_forms import QuestionnaireContext


NOW = datetime(2026, 10, 11, 10, 15, tzinfo=JST)


def test_selection_produces_existing_date_format():
    assert selected_datetime(
        "2026-10-12", "9", "30", now=NOW
    ) == "2026-10-12 09:30"


@pytest.mark.parametrize(
    ("day", "hour", "minute"),
    (
        ("2026-10-11", "10", "00"),
        ("2026-10-14", "12", "00"),
        ("2026-10-12", "24", "00"),
        ("2026-10-12", "12", "15"),
    ),
)
def test_invalid_or_past_selection_is_rejected(day, hour, minute):
    with pytest.raises(InterviewDateError):
        selected_datetime(day, hour, minute, now=NOW)


def make_view(values=None):
    context = QuestionnaireContext(
        guild_id=100,
        channel_id=500,
        user_id=300,
        session_id="a" * 32,
        is_available=lambda: True,
    )
    return InterviewPreferencesView(
        context,
        on_submit=AsyncMock(),
        initial_values=values,
        clock=lambda: NOW,
    )


@pytest.mark.parametrize(
    ("values", "disabled"),
    (
        (None, True),
        (("2026-10-12 12:00", "", ""), True),
        (
            (
                "2026-10-12 12:00",
                "2026-10-12 13:00",
                "2026-10-13 14:30",
            ),
            False,
        ),
    ),
)
def test_submit_enabled_only_when_all_filled(values, disabled):
    async def scenario():
        view = make_view(values)
        try:
            assert view.children[-1].label == "希望日時を送信する"
            assert view.children[-1].disabled is disabled
            assert len(view.children) == 4
        finally:
            view.stop()

    asyncio.run(scenario())


def test_modal_has_day_hour_minute_and_preserves_initial_value():
    async def scenario():
        view = make_view(("2026-10-12 21:30", "", ""))
        modal = PreferenceModal(view, 0, 0)
        try:
            assert len(modal.children) == 3
            assert len(modal._day.options) == 3
            assert len(modal._hour.options) == 24
            assert len(modal._minute.options) == 2
            assert [
                item.value for item in modal._hour.options if item.default
            ] == ["21"]
            assert [
                item.value for item in modal._minute.options if item.default
            ] == ["30"]
            assert "修正する" in view.children[0].label
            modal.to_dict()
        finally:
            modal.stop()
            view.stop()

    asyncio.run(scenario())
