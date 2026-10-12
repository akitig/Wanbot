import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wanbot.welcome.interview_gate import InterviewNotReady
from wanbot.welcome.meeting_voice_cleanup import MeetingVoiceCleanup


SID = "a" * 32
PROPOSAL = "b" * 32


def setup_cleanup():
    welcome = SimpleNamespace(history=AsyncMock(return_value=[]))
    cleanup = MeetingVoiceCleanup(
        client=SimpleNamespace(),
        config=None,
        guild_id=100,
        voices=SimpleNamespace(),
        welcome_repository=welcome,
        is_available=lambda: True,
        store=SimpleNamespace(close=AsyncMock()),
    )
    change = {
        "guild_id": 100,
        "session_id": SID,
        "user_id": 300,
        "channel_id": 500,
        "proposal_id": PROPOSAL,
        "status": "completed",
    }
    changes = SimpleNamespace(
        for_session=AsyncMock(return_value=[change])
    )
    cleanup.set_schedule_changes(changes)
    record = {
        "proposal_id": PROPOSAL,
        "channel_id": 600,
        "identity": {
            "session_id": SID,
            "guild_id": 100,
            "welcome_channel_id": 500,
            "participants": [300, 400],
            "category_id": 700,
        },
    }
    return cleanup, welcome, changes, change, record


def test_completed_change_retires_old_vc():
    async def run():
        cleanup, _, changes, _, record = setup_cleanup()
        assert await cleanup._finished(record) is True
        changes.for_session.assert_awaited_once_with(SID)

    asyncio.run(run())


def test_pending_change_does_not_retire_vc():
    async def run():
        cleanup, _, _, change, record = setup_cleanup()
        change["status"] = "pending"
        assert await cleanup._finished(record) is False

    asyncio.run(run())


def test_new_meeting_vc_is_not_retired():
    async def run():
        cleanup, _, _, _, record = setup_cleanup()
        record["proposal_id"] = "d" * 32
        assert await cleanup._finished(record) is False

    asyncio.run(run())


def test_change_without_plan_does_not_retire_vc():
    async def run():
        cleanup, _, _, change, record = setup_cleanup()
        change["proposal_id"] = None
        assert await cleanup._finished(record) is False

    asyncio.run(run())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("guild_id", 999),
        ("session_id", "e" * 32),
        ("user_id", 999),
        ("channel_id", 999),
    ],
)
def test_mismatched_change_is_rejected(field, value):
    async def run():
        cleanup, _, _, change, record = setup_cleanup()
        change[field] = value
        with pytest.raises(InterviewNotReady):
            await cleanup._finished(record)

    asyncio.run(run())


def test_existing_closed_session_still_retires_vc():
    async def run():
        cleanup, welcome, changes, _, record = setup_cleanup()
        welcome.history.return_value = [
            SimpleNamespace(
                session_id=SID, channel_id=500, state="closed",
            )
        ]
        assert await cleanup._finished(record) is True
        changes.for_session.assert_not_awaited()

    asyncio.run(run())


def test_without_change_repository_active_vc_is_kept():
    async def run():
        cleanup, _, _, _, record = setup_cleanup()
        cleanup.set_schedule_changes(None)
        assert await cleanup._finished(record) is False

    asyncio.run(run())
