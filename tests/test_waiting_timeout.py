"""72時間の承認依頼と、回答後の古い依頼の無効化。"""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wanbot.welcome.interview_gate import InterviewNotReady
from wanbot.welcome.kick_execution import KickTargetChanged
from wanbot.welcome.waiting_timeout import WaitingTimeout


SID = "a" * 32
NOW = datetime(2030, 1, 4, tzinfo=timezone.utc)


def setup():
    clock = {
        "owner": "applicant",
        "epoch": "b" * 32,
        "progress_key": "c" * 64,
    }
    context = SimpleNamespace(
        guild_id=100, session_id=SID, user_id=300, channel_id=500
    )
    saved = []

    async def for_session(session_id):
        assert session_id == SID
        return [dict(item) for item in saved]

    async def create(**kwargs):
        record = {
            "request_id": "d" * 32,
            "guild_id": 100,
            "session_id": SID,
            "user_id": 300,
            "channel_id": 500,
            "status": "pending",
            "waiting_timeout": dict(kwargs["waiting_timeout"]),
        }
        saved.append(record)
        return dict(record)

    async def transition(request_id, *, expected, target):
        record = next(item for item in saved if item["request_id"] == request_id)
        assert record["status"] == expected
        record["status"] = target
        return dict(record)

    repository = SimpleNamespace(
        for_session=AsyncMock(side_effect=for_session),
        create=AsyncMock(side_effect=create),
        transition=AsyncMock(side_effect=transition),
    )
    channel = SimpleNamespace(guild=SimpleNamespace(
        fetch_member=AsyncMock(return_value=SimpleNamespace(joined_at=NOW))
    ))
    review = SimpleNamespace(
        _repository=repository,
        _posted=set(),
        _channel=AsyncMock(return_value=(channel, object())),
    )

    async def publish(record, channel, guild_config):
        review._posted.add((record["request_id"], record["status"]))

    review._publish = AsyncMock(side_effect=publish)
    controller = SimpleNamespace(
        _context=context,
        _kick_review=review,
        _lock=asyncio.Lock(),
        _session=AsyncMock(),
        _client=SimpleNamespace(user=SimpleNamespace(id=999)),
        _clock=lambda: NOW,
    )
    monitor = SimpleNamespace(
        _controller=controller,
        available=lambda: True,
        candidate_locked=AsyncMock(return_value=(clock, "review")),
    )
    return WaitingTimeout(monitor=monitor), monitor, review, saved, clock


def test_creates_one_request_with_timeout_metadata():
    async def scenario():
        service, monitor, review, saved, clock = setup()
        await service.refresh()
        await service.refresh()

        assert len(saved) == 1
        review._repository.create.assert_awaited_once()
        options = review._repository.create.call_args.kwargs
        assert options["actor_id"] == 999
        assert options["waiting_timeout"] == {
            "epoch": clock["epoch"],
            "progress_key": clock["progress_key"],
        }
        review._publish.assert_awaited_once()

    asyncio.run(scenario())


def test_cancelled_request_is_not_recreated():
    async def scenario():
        service, monitor, review, saved, clock = setup()
        await service.refresh()
        saved[0]["status"] = "cancelled"

        await service.refresh()

        assert len(saved) == 1
        review._repository.create.assert_awaited_once()

    asyncio.run(scenario())


def test_saved_progress_invalidates_pending_request():
    async def scenario():
        service, monitor, review, saved, clock = setup()
        await service.refresh()
        monitor.candidate_locked.return_value = (
            {**clock, "epoch": "e" * 32, "progress_key": "f" * 64},
            None,
        )

        await service.refresh()

        assert saved[0]["status"] == "obsolete"
        review._repository.transition.assert_awaited_once()
        review._repository.create.assert_awaited_once()

    asyncio.run(scenario())


def test_approval_rejects_old_progress():
    async def scenario():
        service, monitor, review, saved, clock = setup()
        await service.refresh()
        monitor.candidate_locked.return_value = (
            {**clock, "progress_key": "e" * 64},
            "review",
        )

        with pytest.raises(KickTargetChanged):
            await service.validate_locked(saved[0])

    asyncio.run(scenario())


def test_lookup_failure_holds_execution_without_invalidating_request():
    async def scenario():
        service, monitor, review, saved, clock = setup()
        await service.refresh()
        monitor.candidate_locked.return_value = (
            {**clock, "owner": "unknown"}, None
        )

        await service.refresh()
        assert saved[0]["status"] == "pending"

        with pytest.raises(InterviewNotReady):
            await service.validate_locked(saved[0])

        review._repository.transition.assert_not_awaited()

    asyncio.run(scenario())


def test_current_overdue_request_passes_recheck():
    async def scenario():
        service, monitor, review, saved, clock = setup()
        await service.refresh()
        assert await service.validate_locked(saved[0]) is True

    asyncio.run(scenario())


def test_progress_before_creation_prevents_request():
    async def scenario():
        service, monitor, review, saved, clock = setup()
        monitor.candidate_locked.side_effect = [
            (clock, "review"),
            ({**clock, "epoch": "e" * 32}, None),
        ]

        await service.refresh()

        assert saved == []
        review._repository.create.assert_not_awaited()

    asyncio.run(scenario())


def test_post_failure_retries_saved_request():
    async def scenario():
        service, monitor, review, saved, clock = setup()
        publish = review._publish.side_effect
        review._publish.side_effect = RuntimeError("temporary failure")

        with pytest.raises(RuntimeError):
            await service.refresh()

        assert len(saved) == 1
        review._publish.side_effect = publish
        await service.refresh()

        assert len(saved) == 1
        review._repository.create.assert_awaited_once()
        assert review._publish.await_count == 2

    asyncio.run(scenario())
