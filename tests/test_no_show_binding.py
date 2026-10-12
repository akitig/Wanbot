"""不参加確認中の参加承認・再入力の制限。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wanbot.welcome.no_show_binding import NoShowBinding
from wanbot.welcome.no_show_repository import NoShowConflict


PROPOSAL = "b" * 32


def setup(status="waiting"):
    record = {
        "proposal_id": PROPOSAL,
        "session_id": "a" * 32,
        "guild_id": 100,
        "user_id": 300,
        "channel_id": 500,
        "status": status,
    }
    controller = SimpleNamespace(
        _context=SimpleNamespace(
            session_id="a" * 32,
            guild_id=100,
            user_id=300,
            channel_id=500,
        ),
        _repository=SimpleNamespace(
            archived_for_reschedule=AsyncMock(return_value=None),
        ),
        _meetings=SimpleNamespace(
            archived_for_reschedule=AsyncMock(return_value=None),
        ),
        _kick_review=SimpleNamespace(
            _repository=SimpleNamespace(
                for_session=AsyncMock(return_value=[])
            ),
        ),
    )
    repository = SimpleNamespace(
        get=AsyncMock(return_value=record),
        all_records=AsyncMock(return_value=[record]),
    )
    binding = NoShowBinding(
        controller=controller, repository=repository
    )
    return binding, controller, repository


@pytest.mark.parametrize(
    "status",
    ["waiting", "second_confirmation", "reschedule_requested", "kick_required"],
)
def test_no_show_blocks_admission(status):
    async def scenario():
        binding, controller, repository = setup(status)
        with pytest.raises(NoShowConflict):
            await binding.guard_admission(PROPOSAL)

    asyncio.run(scenario())


def test_cancelled_no_show_review_allows_manager_admission():
    async def scenario():
        binding, controller, repository = setup("kick_required")
        controller._kick_review._repository.for_session.return_value = [
            {
                "status": "cancelled",
                "reason": f"不参加（面談ID：{PROPOSAL}）",
            }
        ]
        await binding.guard_admission(PROPOSAL)

    asyncio.run(scenario())


def test_incomplete_reschedule_blocks_input():
    async def scenario():
        binding, controller, repository = setup("reschedule_requested")
        controller._repository.archived_for_reschedule.return_value = object()

        with pytest.raises(NoShowConflict):
            await binding.guard_input()

    asyncio.run(scenario())


def test_completed_reschedule_allows_input():
    async def scenario():
        binding, controller, repository = setup("reschedule_requested")
        controller._repository.archived_for_reschedule.return_value = object()
        controller._meetings.archived_for_reschedule.return_value = object()

        await binding.guard_input()
        assert await binding.has_rescheduled()

    asyncio.run(scenario())
