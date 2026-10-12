"""不参加の回答から、再調整・承認依頼への接続を確認する。"""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wanbot.welcome.no_show_flow import NoShowFlow
from wanbot.welcome.no_show_repository import NoShowConflict


PROPOSAL = "b" * 32
NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


def setup(status="waiting"):
    context = SimpleNamespace(
        guild_id=100,
        session_id="a" * 32,
        user_id=300,
        channel_id=500,
    )
    plan = SimpleNamespace(
        proposal_id=PROPOSAL,
        welcome_session_id=context.session_id,
        guild_id=100,
        user_id=300,
        channel_id=500,
        status="confirmed",
    )
    request = SimpleNamespace(
        session_id=context.session_id,
        user_id=300,
        channel_id=500,
    )
    record = {
        "proposal_id": PROPOSAL,
        "session_id": context.session_id,
        "guild_id": 100,
        "user_id": 300,
        "channel_id": 500,
        "status": status,
    }
    controller = SimpleNamespace(
        _context=context,
        _session=AsyncMock(),
        _admissions=SimpleNamespace(get=AsyncMock(return_value=None)),
        _plan=AsyncMock(return_value=plan),
        _repository=SimpleNamespace(
            get=AsyncMock(return_value=request),
            archived_for_reschedule=AsyncMock(return_value=None),
            archive_for_reschedule=AsyncMock(),
        ),
        _meetings=SimpleNamespace(
            archived_for_reschedule=AsyncMock(return_value=None),
            archive_for_reschedule=AsyncMock(),
        ),
        _kick_review=SimpleNamespace(
            _repository=SimpleNamespace(
                for_session=AsyncMock(return_value=[])
            ),
            request_no_show=AsyncMock(),
        ),
        _published_meeting=("old", "confirmed"),
    )
    repository = SimpleNamespace(
        get=AsyncMock(return_value=record),
        respond=AsyncMock(),
        expire=AsyncMock(return_value=record),
        confirm_second=AsyncMock(),
        begin=AsyncMock(),
    )
    flow = NoShowFlow(controller=controller, repository=repository)
    return flow, controller, repository, record, plan, request


def test_yes_archives_request_before_plan():
    async def scenario():
        flow, controller, repository, record, plan, request = setup()
        repository.respond.return_value = {
            **record, "status": "reschedule_requested"
        }
        order = []

        async def archive_request(value, *, proposal_id):
            assert value is request
            assert proposal_id == PROPOSAL
            order.append("request")

        async def archive_plan(value):
            assert value is plan
            order.append("plan")

        controller._repository.archive_for_reschedule.side_effect = (
            archive_request
        )
        controller._meetings.archive_for_reschedule.side_effect = archive_plan

        result = await flow.answer_locked(
            proposal_id=PROPOSAL,
            actor_id=300,
            reschedule=True,
            now=NOW,
        )

        assert result["status"] == "reschedule_requested"
        assert order == ["request", "plan"]
        assert controller._published_meeting is None
        controller._kick_review.request_no_show.assert_not_awaited()

    asyncio.run(scenario())


def test_resume_after_request_archived_and_plan_archive_failed():
    async def scenario():
        flow, controller, repository, record, plan, request = setup(
            "reschedule_requested"
        )

        async def archive_request(value, *, proposal_id):
            controller._repository.archived_for_reschedule.return_value = value

        controller._repository.archive_for_reschedule.side_effect = (
            archive_request
        )
        controller._meetings.archive_for_reschedule.side_effect = RuntimeError(
            "temporary failure"
        )

        with pytest.raises(RuntimeError):
            await flow.refresh_locked(proposal_id=PROPOSAL, now=NOW)

        controller._meetings.archive_for_reschedule.side_effect = None
        await flow.refresh_locked(proposal_id=PROPOSAL, now=NOW)

        controller._repository.archive_for_reschedule.assert_awaited_once()
        assert controller._meetings.archive_for_reschedule.await_count == 2
        controller._meetings.archive_for_reschedule.assert_awaited_with(plan)
        repository.expire.assert_not_awaited()

    asyncio.run(scenario())


def test_completed_reschedule_does_not_touch_new_submission():
    async def scenario():
        flow, controller, repository, record, plan, request = setup(
            "reschedule_requested"
        )
        controller._meetings.archived_for_reschedule.return_value = plan
        controller._repository.archived_for_reschedule.return_value = request

        await flow.refresh_locked(proposal_id=PROPOSAL, now=NOW)

        controller._plan.assert_not_awaited()
        controller._repository.get.assert_not_awaited()
        controller._repository.archive_for_reschedule.assert_not_awaited()
        controller._meetings.archive_for_reschedule.assert_not_awaited()

    asyncio.run(scenario())


def test_no_sends_review_request():
    async def scenario():
        flow, controller, repository, record, plan, request = setup()
        result = {**record, "status": "kick_required", "reason": "declined"}
        repository.respond.return_value = result

        await flow.answer_locked(
            proposal_id=PROPOSAL,
            actor_id=300,
            reschedule=False,
            now=NOW,
        )

        controller._kick_review.request_no_show.assert_awaited_once_with(
            controller, result
        )
        controller._repository.archive_for_reschedule.assert_not_awaited()

    asyncio.run(scenario())


def test_deadline_sends_review_request():
    async def scenario():
        flow, controller, repository, record, plan, request = setup()
        result = {**record, "status": "kick_required", "reason": "timeout"}
        repository.expire.return_value = result

        await flow.refresh_locked(proposal_id=PROPOSAL, now=NOW)

        repository.expire.assert_awaited_once_with(PROPOSAL, now=NOW)
        controller._kick_review.request_no_show.assert_awaited_once_with(
            controller, result
        )

    asyncio.run(scenario())


def test_second_confirmation_sends_review_request():
    async def scenario():
        flow, controller, repository, record, plan, request = setup(
            "second_confirmation"
        )
        result = {
            **record,
            "status": "kick_required",
            "reason": "second_no_show",
        }
        repository.confirm_second.return_value = result

        await flow.confirm_second_locked(
            proposal_id=PROPOSAL, actor_id=400
        )

        repository.confirm_second.assert_awaited_once_with(
            PROPOSAL, actor_id=400
        )
        controller._kick_review.request_no_show.assert_awaited_once_with(
            controller, result
        )

    asyncio.run(scenario())


def test_other_user_cannot_answer():
    async def scenario():
        flow, controller, repository, record, plan, request = setup()

        with pytest.raises(NoShowConflict):
            await flow.answer_locked(
                proposal_id=PROPOSAL,
                actor_id=999,
                reschedule=True,
                now=NOW,
            )

        repository.respond.assert_not_awaited()

    asyncio.run(scenario())


def test_admission_in_progress_blocks_no_show_processing():
    async def scenario():
        flow, controller, repository, record, plan, request = setup()
        controller._admissions.get.return_value = {"phase": "approved"}

        with pytest.raises(NoShowConflict):
            await flow.refresh_locked(proposal_id=PROPOSAL, now=NOW)

        repository.expire.assert_not_awaited()
        controller._kick_review.request_no_show.assert_not_awaited()

    asyncio.run(scenario())


def test_existing_kick_request_blocks_rescheduling():
    async def scenario():
        flow, controller, repository, record, plan, request = setup()
        controller._kick_review._repository.for_session.return_value = [
            {"status": "pending"}
        ]

        with pytest.raises(NoShowConflict):
            await flow.answer_locked(
                proposal_id=PROPOSAL,
                actor_id=300,
                reschedule=True,
                now=NOW,
            )

        repository.respond.assert_not_awaited()
        controller._repository.archive_for_reschedule.assert_not_awaited()

    asyncio.run(scenario())
