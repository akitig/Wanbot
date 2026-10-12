import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wanbot.welcome.interview_dates import InterviewDateError
from wanbot.welcome.interview_picker import (
    InterviewDatePicker,
    day_options,
    validate_picker_dates,
)


NOW = datetime(2026, 10, 11, 7, 0, tzinfo=timezone.utc)
VALUES = (
    "2026-10-11 18:00",
    "2026-10-12 19:30",
    "2026-10-13 20:00",
)


def test_days_use_japanese_calendar():
    assert day_options(NOW) == (
        ("今日（10/11）", "2026-10-11"),
        ("明日（10/12）", "2026-10-12"),
        ("明後日（10/13）", "2026-10-13"),
    )
    later = datetime(2026, 10, 11, 15, 0, tzinfo=timezone.utc)
    assert day_options(later)[0][1] == "2026-10-12"


def test_valid_selection_is_accepted():
    assert len(validate_picker_dates(VALUES, now=NOW)) == 3


@pytest.mark.parametrize(
    "value",
    [
        "2026-10-14 18:00",
        "2026-10-11 15:30",
        "2026-10-11 18:15",
    ],
)
def test_invalid_selection_is_rejected(value):
    with pytest.raises(InterviewDateError):
        validate_picker_dates((value, VALUES[1], VALUES[2]), now=NOW)


def test_dropdowns_submit_three_preferences():
    async def scenario():
        handler = AsyncMock()
        context = SimpleNamespace(
            guild_id=100,
            check=AsyncMock(return_value=True),
        )
        interaction = SimpleNamespace(
            response=SimpleNamespace(
                defer=AsyncMock(),
                is_done=lambda: True,
            ),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )
        view = InterviewDatePicker(
            context,
            on_submit=handler,
            clock=lambda: NOW,
        )
        try:
            for day, hour, minute in (
                ("2026-10-11", "18", "0"),
                ("2026-10-12", "19", "30"),
                ("2026-10-13", "20", "0"),
            ):
                for index, value in enumerate((day, hour, minute)):
                    menu = view.children[index]
                    menu._values = [value]
                    await menu.callback(interaction)
                await view.children[3].callback(interaction)

            handler.assert_awaited_once_with(interaction, VALUES)
        finally:
            view.stop()

    asyncio.run(scenario())
