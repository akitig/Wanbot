import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from wanbot.welcome.interview_controller import InterviewController
from wanbot.welcome.interview_gate import InterviewNotReady


NOW = datetime(2026, 10, 11, 7, 0, tzinfo=timezone.utc)
VALUES = (
    "2026-10-11 18:00",
    "2026-10-12 19:00",
    "2026-10-13 20:00",
)


def make_case():
    context = SimpleNamespace(
        guild_id=100,
        user_id=300,
        channel_id=500,
        session_id="a" * 32,
        is_available=lambda: True,
        check=AsyncMock(return_value=True),
    )
    session = SimpleNamespace(
        state="active",
        session_id=context.session_id,
        user_id=300,
        channel_id=500,
    )
    gate = SimpleNamespace(check=AsyncMock())
    repository = SimpleNamespace(
        get=AsyncMock(return_value=None),
        confirm=AsyncMock(return_value=object()),
    )
    controller = InterviewController(
        client=None,
        context=context,
        welcome_repository=SimpleNamespace(
            get_active=AsyncMock(return_value=session)
        ),
        repository=repository,
        gate=gate,
        lock=asyncio.Lock(),
        clock=lambda: NOW,
    )
    controller._show_saved = AsyncMock()
    interaction = SimpleNamespace(edit_original_response=AsyncMock())
    return controller, context, session, gate, repository, interaction


def test_confirm_checks_conditions_before_saving():
    async def scenario():
        controller, _, session, gate, repository, interaction = make_case()
        events = []

        async def check():
            events.append("check")

        async def save(*args, **kwargs):
            events.append("save")
            return object()

        gate.check.side_effect = check
        repository.confirm.side_effect = save
        try:
            await controller.confirm(interaction, VALUES)
            assert events == ["check", "save"]
            repository.confirm.assert_awaited_once_with(
                session, VALUES, now=NOW
            )
            controller._show_saved.assert_awaited_once()
        finally:
            controller.close()

    asyncio.run(scenario())


def test_unmet_condition_does_not_save():
    async def scenario():
        controller, _, _, gate, repository, interaction = make_case()
        gate.check.side_effect = InterviewNotReady("診断を完了してください。")
        try:
            await controller.confirm(interaction, VALUES)
            repository.confirm.assert_not_awaited()
            controller._show_saved.assert_not_awaited()
            assert "診断を完了" in (
                interaction.edit_original_response.call_args.kwargs["content"]
            )
        finally:
            controller.close()

    asyncio.run(scenario())


def test_already_confirmed_request_is_not_overwritten():
    async def scenario():
        controller, _, _, gate, repository, interaction = make_case()
        saved = object()
        repository.get.return_value = saved
        try:
            await controller.confirm(interaction, VALUES)
            repository.confirm.assert_not_awaited()
            gate.check.assert_not_awaited()
            controller._show_saved.assert_awaited_once_with(
                interaction, saved
            )
        finally:
            controller.close()

    asyncio.run(scenario())


def test_wrong_actor_cannot_submit():
    async def scenario():
        controller, context, _, gate, repository, interaction = make_case()
        context.check.return_value = False
        try:
            await controller.confirm(interaction, VALUES)
            gate.check.assert_not_awaited()
            repository.confirm.assert_not_awaited()
        finally:
            controller.close()

    asyncio.run(scenario())


def test_date_is_rechecked_after_condition_lookup():
    async def scenario():
        controller, _, _, gate, repository, interaction = make_case()

        async def check():
            controller._clock = lambda: datetime(
                2026, 10, 15, tzinfo=timezone.utc
            )

        gate.check.side_effect = check
        try:
            await controller.confirm(interaction, VALUES)
            repository.confirm.assert_not_awaited()
            assert "現在より後" in (
                interaction.edit_original_response.call_args.kwargs["content"]
            )
        finally:
            controller.close()

    asyncio.run(scenario())
