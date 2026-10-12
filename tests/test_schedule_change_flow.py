import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wanbot.welcome.schedule_change_flow import ScheduleChangeFlow
from wanbot.welcome.schedule_change_repository import ScheduleChangeConflict


NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)
SID = "a" * 32
CHANGE = "c" * 32
PROPOSAL = "b" * 32


def setup_flow(*, has_plan=True):
    record = {
        "change_id": CHANGE,
        "guild_id": 100,
        "session_id": SID,
        "user_id": 300,
        "channel_id": 500,
        "submission_at": NOW.isoformat(),
        "proposal_id": PROPOSAL if has_plan else None,
        "status": "pending",
    }
    request = SimpleNamespace(
        session_id=SID, user_id=300, channel_id=500,
        confirmed_at=NOW,
    )
    plan = SimpleNamespace(
        proposal_id=PROPOSAL,
        welcome_session_id=SID,
        guild_id=100, user_id=300, channel_id=500,
    )
    repository = SimpleNamespace(
        for_session=AsyncMock(return_value=[record]),
        complete=AsyncMock(return_value=dict(record, status="completed")),
    )
    controller = SimpleNamespace(
        _context=SimpleNamespace(
            guild_id=100, session_id=SID, user_id=300, channel_id=500,
        ),
        _session=AsyncMock(),
        _admissions=SimpleNamespace(get=AsyncMock(return_value=None)),
        _kick_review=SimpleNamespace(
            _repository=SimpleNamespace(
                for_session=AsyncMock(return_value=[])
            ),
        ),
        _no_show=SimpleNamespace(guard_input=AsyncMock()),
        _repository=SimpleNamespace(
            archived_for_reschedule=AsyncMock(return_value=None),
            archive_for_reschedule=AsyncMock(),
        ),
        _meetings=SimpleNamespace(
            archived_for_schedule_change=AsyncMock(return_value=None),
            archive_for_schedule_change=AsyncMock(),
        ),
        load_request=AsyncMock(return_value=request),
        _plan=AsyncMock(return_value=plan if has_plan else None),
        _published_meeting=("old", "confirmed"),
    )
    flow = ScheduleChangeFlow(
        controller=controller, repository=repository
    )
    return SimpleNamespace(
        flow=flow, controller=controller, repository=repository,
        record=record, request=request, plan=plan,
    )


def test_archives_request_before_plan_and_completion():
    async def run():
        item = setup_flow()
        events = []

        async def archive_request(*args, **kwargs):
            events.append("request")

        async def archive_plan(*args, **kwargs):
            events.append("plan")

        async def complete(*args, **kwargs):
            events.append("complete")
            return dict(item.record, status="completed")

        item.controller._repository.archive_for_reschedule.side_effect = (
            archive_request
        )
        item.controller._meetings.archive_for_schedule_change.side_effect = (
            archive_plan
        )
        item.repository.complete.side_effect = complete

        await item.flow.apply_locked(CHANGE)
        assert events == ["request", "plan", "complete"]
        assert item.controller._published_meeting is None

    asyncio.run(run())


def test_change_before_plan_selection():
    async def run():
        item = setup_flow(has_plan=False)
        await item.flow.apply_locked(CHANGE)
        item.controller._repository.archive_for_reschedule.assert_awaited_once()
        item.controller._meetings.archive_for_schedule_change.assert_not_awaited()
        item.repository.complete.assert_awaited_once_with(CHANGE)

    asyncio.run(run())


def test_resume_after_request_was_archived():
    async def run():
        item = setup_flow()
        item.controller._repository.archived_for_reschedule.return_value = (
            item.request
        )
        await item.flow.apply_locked(CHANGE)
        item.controller.load_request.assert_not_awaited()
        item.controller._repository.archive_for_reschedule.assert_not_awaited()
        item.controller._meetings.archive_for_schedule_change.assert_awaited_once()

    asyncio.run(run())


def test_resume_after_both_archives_does_not_touch_new_plan():
    async def run():
        item = setup_flow()
        item.controller._repository.archived_for_reschedule.return_value = (
            item.request
        )
        item.controller._meetings.archived_for_schedule_change.return_value = (
            item.plan
        )
        await item.flow.apply_locked(CHANGE)
        item.controller._plan.assert_not_awaited()
        item.controller.load_request.assert_not_awaited()
        item.controller._meetings.archive_for_schedule_change.assert_not_awaited()
        item.repository.complete.assert_awaited_once()

    asyncio.run(run())


def test_changed_plan_blocks_request_archive():
    async def run():
        item = setup_flow()
        item.plan.proposal_id = "d" * 32
        with pytest.raises(ScheduleChangeConflict):
            await item.flow.apply_locked(CHANGE)
        item.controller._repository.archive_for_reschedule.assert_not_awaited()
        item.repository.complete.assert_not_awaited()

    asyncio.run(run())


def test_completed_change_does_not_touch_current_state():
    async def run():
        item = setup_flow()
        item.record["status"] = "completed"
        await item.flow.apply_locked(CHANGE)
        item.controller._session.assert_not_awaited()
        item.controller.load_request.assert_not_awaited()
        item.repository.complete.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize("blocker", ["admission", "kick"])
def test_approval_or_kick_blocks_change(blocker):
    async def run():
        item = setup_flow()
        if blocker == "admission":
            item.controller._admissions.get.return_value = {"phase": "approved"}
        else:
            item.controller._kick_review._repository.for_session.return_value = [
                {"status": "pending"}
            ]

        with pytest.raises(ScheduleChangeConflict):
            await item.flow.apply_locked(CHANGE)
        item.controller._repository.archive_for_reschedule.assert_not_awaited()
        item.repository.complete.assert_not_awaited()

    asyncio.run(run())


def test_missing_request_history_blocks_completion():
    async def run():
        item = setup_flow()
        item.controller._meetings.archived_for_schedule_change.return_value = (
            item.plan
        )
        with pytest.raises(ScheduleChangeConflict):
            await item.flow.apply_locked(CHANGE)
        item.repository.complete.assert_not_awaited()

    asyncio.run(run())
