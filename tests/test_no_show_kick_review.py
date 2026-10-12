"""面談不参加からのキック承認依頼。"""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wanbot.welcome.interview_gate import InterviewNotReady
from wanbot.welcome.kick_review import KickReview


def setup():
    context = SimpleNamespace(
        guild_id=100,
        session_id="a" * 32,
        user_id=300,
        channel_id=500,
    )
    controller = SimpleNamespace(
        _context=context,
        _session=AsyncMock(),
        _plan=AsyncMock(return_value=SimpleNamespace(
            status="confirmed",
            proposal_id="b" * 32,
        )),
    )
    no_show = {
        "guild_id": 100,
        "session_id": "a" * 32,
        "user_id": 300,
        "channel_id": 500,
        "proposal_id": "b" * 32,
        "status": "kick_required",
        "reason": "timeout",
        "reported_by": 400,
        "confirmed_by": None,
    }
    saved = []

    async def create(**kwargs):
        record = {
            "request_id": "c" * 32,
            "guild_id": context.guild_id,
            "session_id": context.session_id,
            "user_id": context.user_id,
            "channel_id": context.channel_id,
            "reason": kwargs["reason"],
            "requested_by": kwargs["actor_id"],
            "status": "pending",
        }
        saved.append(record)
        return record

    async def for_session(session_id):
        assert session_id == context.session_id
        return list(saved)

    member = SimpleNamespace(
        joined_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        kick=AsyncMock(),
    )
    channel = SimpleNamespace(
        guild=SimpleNamespace(fetch_member=AsyncMock(return_value=member))
    )
    review = KickReview.__new__(KickReview)
    review._guild_id = 100
    review._admissions = SimpleNamespace(get=AsyncMock(return_value=None))
    review._repository = SimpleNamespace(
        create=AsyncMock(side_effect=create),
        for_session=AsyncMock(side_effect=for_session),
    )
    review._posted = set()
    review._channel = AsyncMock(return_value=(channel, object()))

    async def publish(record, channel, guild_config):
        review._posted.add((record["request_id"], record["status"]))

    review._publish = AsyncMock(side_effect=publish)
    return review, controller, no_show, saved, member


@pytest.mark.parametrize(
    "reason,confirmed_by,expected_actor",
    [
        ("timeout", None, 400),
        ("declined", None, 400),
        ("second_no_show", 401, 401),
    ],
)
def test_creates_review_without_kicking(reason, confirmed_by, expected_actor):
    async def scenario():
        review, controller, record, saved, member = setup()
        record.update(reason=reason, confirmed_by=confirmed_by)

        result = await review.request_no_show(controller, record)

        assert result["status"] == "pending"
        assert result["requested_by"] == expected_actor
        assert record["proposal_id"] in result["reason"]
        assert len(saved) == 1
        review._publish.assert_awaited_once()
        member.kick.assert_not_awaited()

    asyncio.run(scenario())


def test_repeated_request_reuses_existing_review():
    async def scenario():
        review, controller, record, saved, member = setup()

        first = await review.request_no_show(controller, record)
        second = await review.request_no_show(controller, record)

        assert second == first
        assert len(saved) == 1
        review._repository.create.assert_awaited_once()
        review._publish.assert_awaited_once()
        member.kick.assert_not_awaited()

    asyncio.run(scenario())


def test_cancelled_review_is_not_recreated():
    async def scenario():
        review, controller, record, saved, member = setup()
        await review.request_no_show(controller, record)
        saved[0]["status"] = "cancelled"
        review._publish.reset_mock()

        result = await review.request_no_show(controller, record)

        assert result["status"] == "cancelled"
        assert len(saved) == 1
        review._repository.create.assert_awaited_once()
        review._publish.assert_not_awaited()
        member.kick.assert_not_awaited()

    asyncio.run(scenario())


def test_publish_failure_retries_same_saved_review():
    async def scenario():
        review, controller, record, saved, member = setup()
        publish = review._publish.side_effect
        review._publish.side_effect = RuntimeError("temporary failure")

        with pytest.raises(RuntimeError):
            await review.request_no_show(controller, record)

        assert len(saved) == 1
        review._publish.side_effect = publish
        result = await review.request_no_show(controller, record)

        assert result["request_id"] == saved[0]["request_id"]
        review._repository.create.assert_awaited_once()
        assert review._publish.await_count == 2
        member.kick.assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "waiting"},
        {"guild_id": 999},
        {"session_id": "d" * 32},
        {"user_id": 999},
        {"channel_id": 999},
        {"proposal_id": "d" * 32},
        {"reason": "unknown"},
    ],
)
def test_wrong_state_or_target_is_rejected(changes):
    async def scenario():
        review, controller, record, saved, member = setup()
        record.update(changes)

        with pytest.raises(InterviewNotReady):
            await review.request_no_show(controller, record)

        assert saved == []
        review._repository.create.assert_not_awaited()
        review._publish.assert_not_awaited()
        member.kick.assert_not_awaited()

    asyncio.run(scenario())


def test_admitted_member_cannot_get_no_show_kick_request():
    async def scenario():
        review, controller, record, saved, member = setup()
        review._admissions.get.return_value = {"phase": "approved"}

        with pytest.raises(InterviewNotReady):
            await review.request_no_show(controller, record)

        assert saved == []
        review._repository.create.assert_not_awaited()
        member.kick.assert_not_awaited()

    asyncio.run(scenario())
