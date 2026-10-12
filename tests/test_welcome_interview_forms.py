import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from wanbot.welcome.interview_dates import validate_dates
from wanbot.welcome.interview_forms import (
    InterviewDatesModal,
    InterviewDatesPreview,
    preview_text,
)


NOW = datetime(2026, 10, 11, 7, 0, tzinfo=timezone.utc)
VALUES = (
    "2026-10-12 18:00",
    "2026-10-13 19:00",
    "2026-10-14 20:00",
)


def make_context():
    return SimpleNamespace(
        guild_id=100,
        check=AsyncMock(return_value=True),
    )


def make_interaction():
    return SimpleNamespace(
        response=SimpleNamespace(
            is_done=lambda: False,
            defer=AsyncMock(),
            send_message=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def test_valid_input_is_passed_to_preview_handler():
    async def scenario():
        handler = AsyncMock()
        interaction = make_interaction()
        modal = InterviewDatesModal(
            make_context(),
            on_submit=handler,
            clock=lambda: NOW,
        )
        try:
            for field, value in zip(modal.children, VALUES):
                field._value = value
            await modal.on_submit(interaction)
            handler.assert_awaited_once_with(interaction, VALUES)
            interaction.response.defer.assert_awaited_once_with(
                ephemeral=True, thinking=True
            )
        finally:
            modal.stop()

    asyncio.run(scenario())


def test_invalid_input_does_not_advance():
    async def scenario():
        handler = AsyncMock()
        interaction = make_interaction()
        modal = InterviewDatesModal(
            make_context(),
            on_submit=handler,
            clock=lambda: NOW,
        )
        try:
            for field, value in zip(modal.children, VALUES):
                field._value = value
            modal.children[0]._value = "明日の夜"
            await modal.on_submit(interaction)

            handler.assert_not_awaited()
            interaction.response.send_message.assert_awaited_once()
            assert interaction.response.send_message.call_args.kwargs[
                "ephemeral"
            ] is True
        finally:
            modal.stop()

    asyncio.run(scenario())


def test_edit_form_preserves_previous_values():
    async def scenario():
        modal = InterviewDatesModal(
            make_context(),
            on_submit=AsyncMock(),
            initial_values=VALUES,
        )
        try:
            assert tuple(field.default for field in modal.children) == VALUES
        finally:
            modal.stop()

    asyncio.run(scenario())


def test_confirm_passes_the_displayed_preferences():
    async def scenario():
        handler = AsyncMock()
        interaction = make_interaction()
        view = InterviewDatesPreview(
            make_context(),
            VALUES,
            on_confirm=handler,
            on_edit=AsyncMock(),
        )
        try:
            await view.confirm(interaction)
            handler.assert_awaited_once_with(interaction, VALUES)
        finally:
            view.stop()

    asyncio.run(scenario())


def test_edit_passes_previous_preferences_without_confirming():
    async def scenario():
        confirm = AsyncMock()
        edit = AsyncMock()
        interaction = make_interaction()
        view = InterviewDatesPreview(
            make_context(),
            VALUES,
            on_confirm=confirm,
            on_edit=edit,
        )
        try:
            await view.edit(interaction)
            edit.assert_awaited_once_with(interaction, VALUES)
            confirm.assert_not_awaited()
            interaction.response.defer.assert_not_awaited()
        finally:
            view.stop()

    asyncio.run(scenario())


def test_rejected_actor_cannot_confirm_or_edit():
    async def scenario():
        context = make_context()
        context.check.return_value = False
        confirm = AsyncMock()
        edit = AsyncMock()
        view = InterviewDatesPreview(
            context,
            VALUES,
            on_confirm=confirm,
            on_edit=edit,
        )
        try:
            interaction = make_interaction()
            await view.confirm(interaction)
            await view.edit(interaction)
            confirm.assert_not_awaited()
            edit.assert_not_awaited()
        finally:
            view.stop()

    asyncio.run(scenario())


def test_preview_explains_preferences_are_not_booking_confirmation():
    dates = validate_dates(VALUES, now=NOW)
    content = preview_text(dates)
    assert "第1希望" in content
    assert "2026年10月12日 18:00（日本時間）" in content
    assert "面談日時はまだ確定していません" in content
