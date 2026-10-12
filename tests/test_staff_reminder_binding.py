import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wanbot.welcome.no_show_repository import NoShowConflict
from wanbot.welcome.staff_reminder_binding import StaffReminderBinding


SID = "a" * 32
NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def setup_binding():
    context = SimpleNamespace(
        guild_id=100,
        session_id=SID,
        user_id=300,
        channel_id=500,
        is_available=lambda: True,
    )
    config = SimpleNamespace(
        admin_role_ids=frozenset({10}),
        ids={"KICK_APPROVAL_CHANNEL_ID": 600},
    )
    session = SimpleNamespace(
        session_id=SID,
        channel_id=500,
        state="active",
    )
    request = SimpleNamespace(
        session_id=SID,
        user_id=300,
        channel_id=500,
        confirmed_at=NOW,
    )
    controller = SimpleNamespace(
        _context=context,
        _closed=False,
        _lock=asyncio.Lock(),
        _clock=lambda: NOW,
        _meeting_config=SimpleNamespace(guild_for=lambda guild_id: config),
        _welcome=SimpleNamespace(get_active=AsyncMock(return_value=session)),
        _admissions=SimpleNamespace(get=AsyncMock(return_value=None)),
        _kick_review=SimpleNamespace(
            _repository=SimpleNamespace(
                for_session=AsyncMock(return_value=[])
            ),
            _channel=AsyncMock(return_value=("private-channel", config)),
        ),
        load_request=AsyncMock(return_value=request),
        _plan=AsyncMock(return_value=None),
        _no_show=SimpleNamespace(guard_input=AsyncMock()),
    )
    binding = StaffReminderBinding(
        controller=controller,
        repository=SimpleNamespace(),
    )
    return binding, controller


def test_submitted_preferences_wait_for_staff():
    async def run():
        binding, controller = setup_binding()
        target = await binding._load_target()
        assert target.kind == "date_selection"
        controller._welcome.get_active.assert_awaited_once_with(300)

    asyncio.run(run())


@pytest.mark.parametrize("state", ["archiving", "closed"])
def test_finished_session_is_not_reminded(state):
    async def run():
        binding, controller = setup_binding()
        controller._welcome.get_active.return_value.state = state
        assert await binding._load_target() is None
        controller.load_request.assert_not_awaited()

    asyncio.run(run())


def test_rejoined_member_does_not_use_old_session():
    async def run():
        binding, controller = setup_binding()
        controller._welcome.get_active.return_value.session_id = "b" * 32
        assert await binding._load_target() is None

    asyncio.run(run())


def test_admission_stops_reminders():
    async def run():
        binding, controller = setup_binding()
        controller._admissions.get.return_value = {"phase": "approved"}
        assert await binding._load_target() is None
        controller.load_request.assert_not_awaited()

    asyncio.run(run())


def test_no_show_answer_wait_is_excluded():
    async def run():
        binding, controller = setup_binding()
        controller._no_show.guard_input.side_effect = NoShowConflict("waiting")
        assert await binding._load_target() is None

    asyncio.run(run())


def test_kick_review_takes_priority_over_no_show():
    async def run():
        binding, controller = setup_binding()
        controller._kick_review._repository.for_session.return_value = [{
            "guild_id": 100,
            "session_id": SID,
            "user_id": 300,
            "channel_id": 500,
            "request_id": "c" * 32,
            "status": "pending",
        }]
        controller._no_show.guard_input.side_effect = NoShowConflict("waiting")
        target = await binding._load_target()
        assert target.kind == "kick_review"
        controller._no_show.guard_input.assert_not_awaited()

        channel, roles = await binding._resolve_channel(target)
        assert channel == "private-channel"
        assert roles == frozenset({10})
        controller._kick_review._channel.assert_awaited_once()

    asyncio.run(run())


def test_closed_controller_is_not_read():
    async def run():
        binding, controller = setup_binding()
        controller._closed = True
        assert await binding._load_target() is None
        controller._welcome.get_active.assert_not_awaited()

    asyncio.run(run())
