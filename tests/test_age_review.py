import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wanbot.welcome.age_review import AgeReview
from wanbot.welcome.interview_gate import InterviewNotReady
from wanbot.welcome.kick_execution import KickTargetChanged


SID = "a" * 32


def setup_review():
    session = SimpleNamespace(
        session_id=SID, user_id=300, channel_id=500, state="active",
    )
    progress = SimpleNamespace(
        status="age_review_required", adult=False, underage_confirmed=True,
    )
    snapshot = SimpleNamespace(
        session_id=SID, user_id=300, progress=progress,
    )
    record = {
        "request_id": "b" * 32,
        "guild_id": 100,
        "session_id": SID,
        "user_id": 300,
        "channel_id": 500,
        "age_review": True,
        "status": "pending",
    }
    member = SimpleNamespace(
        id=300, guild=SimpleNamespace(id=100), bot=False,
        joined_at=datetime(2026, 10, 12, tzinfo=timezone.utc),
    )
    channel = SimpleNamespace(
        guild=SimpleNamespace(fetch_member=AsyncMock(return_value=member))
    )
    repository = SimpleNamespace(
        for_session=AsyncMock(return_value=[]),
        create=AsyncMock(return_value=record),
    )
    review = SimpleNamespace(
        _repository=repository,
        _posted=set(),
        _channel=AsyncMock(return_value=(channel, object())),
        _publish=AsyncMock(),
    )
    questions = SimpleNamespace(get=AsyncMock(return_value=snapshot))
    welcome = SimpleNamespace(get_active=AsyncMock(return_value=session))
    admissions = SimpleNamespace(get=AsyncMock(return_value=None))
    service = AgeReview(
        client=SimpleNamespace(user=SimpleNamespace(id=900)),
        guild_id=100,
        welcome=welcome,
        questions=questions,
        admissions=admissions,
        review=review,
        is_available=lambda: True,
    )
    return SimpleNamespace(
        service=service, session=session, progress=progress,
        questions=questions, welcome=welcome, admissions=admissions,
        review=review, repository=repository, record=record,
    )


def test_confirmed_underage_creates_pending_review():
    async def run():
        item = setup_review()
        await item.service.refresh_session_locked(item.session)
        options = item.repository.create.await_args.kwargs
        assert options["age_review"] is True
        assert options["actor_id"] == 900
        assert options["context"].user_id == 300
        item.review._publish.assert_awaited_once()

    asyncio.run(run())


def test_first_no_answer_without_reconfirmation_does_not_create():
    async def run():
        item = setup_review()
        item.progress.status = "answering"
        item.progress.underage_confirmed = False
        await item.service.refresh_session_locked(item.session)
        item.repository.create.assert_not_awaited()

    asyncio.run(run())


def test_corrected_age_does_not_create():
    async def run():
        item = setup_review()
        item.progress.status = "answering"
        item.progress.adult = True
        item.progress.underage_confirmed = False
        await item.service.refresh_session_locked(item.session)
        item.repository.create.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize("status", ["cancelled", "obsolete", "completed"])
def test_finished_review_is_not_recreated(status):
    async def run():
        item = setup_review()
        item.record["status"] = status
        item.repository.for_session.return_value = [item.record]
        await item.service.refresh_session_locked(item.session)
        item.repository.create.assert_not_awaited()
        item.review._publish.assert_not_awaited()

    asyncio.run(run())


def test_saved_pending_review_is_republished_without_recreation():
    async def run():
        item = setup_review()
        item.repository.for_session.return_value = [item.record]
        await item.service.refresh_session_locked(item.session)
        item.repository.create.assert_not_awaited()
        item.review._publish.assert_awaited_once()

    asyncio.run(run())


def test_changed_answer_blocks_approval():
    async def run():
        item = setup_review()
        item.progress.adult = True
        with pytest.raises(KickTargetChanged):
            await item.service.validate_locked(item.record)

    asyncio.run(run())


def test_missing_answer_blocks_approval():
    async def run():
        item = setup_review()
        item.questions.get.return_value = None
        with pytest.raises(InterviewNotReady):
            await item.service.validate_locked(item.record)

    asyncio.run(run())


def test_different_welcome_blocks_approval():
    async def run():
        item = setup_review()
        item.welcome.get_active.return_value = SimpleNamespace(
            session_id="d" * 32, user_id=300, channel_id=500, state="active",
        )
        with pytest.raises(KickTargetChanged):
            await item.service.validate_locked(item.record)

    asyncio.run(run())


def test_admitted_member_does_not_get_review():
    async def run():
        item = setup_review()
        item.admissions.get.return_value = {"phase": "approved"}
        await item.service.refresh_session_locked(item.session)
        item.repository.create.assert_not_awaited()

    asyncio.run(run())
