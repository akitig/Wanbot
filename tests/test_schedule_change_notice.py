import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from wanbot.welcome.interview_gate import InterviewNotReady
from wanbot.welcome.schedule_change_notice import ScheduleChangeNotice


SID = "a" * 32
CHANGE = "c" * 32


def setup_notice():
    record = {
        "change_id": CHANGE,
        "guild_id": 100,
        "session_id": SID,
        "user_id": 300,
        "channel_id": 500,
        "created_at": "2026-10-12T00:00:00+00:00",
        "status": "completed",
        "source": "manager",
    }
    messages = []
    channel = Mock(spec=discord.TextChannel)
    channel.id = 500
    channel.guild = SimpleNamespace(id=100)
    channel.topic = f"wanbot:welcome:100:{SID}"

    async def history(**kwargs):
        for message in messages:
            yield message

    async def send(**kwargs):
        messages.append(SimpleNamespace(
            author=SimpleNamespace(id=900),
            webhook_id=None,
            embeds=[kwargs["embed"]],
        ))

    channel.history = history
    channel.send = AsyncMock(side_effect=send)
    controller = SimpleNamespace(
        _context=SimpleNamespace(
            guild_id=100, session_id=SID, user_id=300, channel_id=500,
        ),
        _session=AsyncMock(),
        load_request=AsyncMock(return_value=None),
        _plan=AsyncMock(return_value=None),
        _client=SimpleNamespace(
            user=SimpleNamespace(id=900),
            fetch_channel=AsyncMock(return_value=channel),
        ),
    )
    repository = SimpleNamespace(
        for_session=AsyncMock(return_value=[record])
    )
    notice = ScheduleChangeNotice(
        controller=controller, repository=repository
    )
    return SimpleNamespace(
        notice=notice, controller=controller, repository=repository,
        channel=channel, record=record, messages=messages,
    )


def test_manager_request_mentions_only_applicant():
    async def run():
        item = setup_notice()
        await item.notice.refresh_locked()
        options = item.channel.send.await_args.kwargs
        assert "<@300>" in options["content"]
        mentions = options["allowed_mentions"]
        assert [user.id for user in mentions.users] == [300]
        assert mentions.roles is False
        assert mentions.everyone is False

    asyncio.run(run())


def test_member_change_does_not_ping():
    async def run():
        item = setup_notice()
        item.record["source"] = "member"
        await item.notice.refresh_locked()
        options = item.channel.send.await_args.kwargs
        assert options["allowed_mentions"].users is False

    asyncio.run(run())


def test_restored_service_does_not_duplicate_notice():
    async def run():
        item = setup_notice()
        await item.notice.refresh_locked()
        restored = ScheduleChangeNotice(
            controller=item.controller, repository=item.repository
        )
        await restored.refresh_locked()
        assert item.channel.send.await_count == 1

    asyncio.run(run())


def test_resubmission_suppresses_notice():
    async def run():
        item = setup_notice()
        item.controller.load_request.return_value = object()
        await item.notice.refresh_locked()
        item.channel.send.assert_not_awaited()

    asyncio.run(run())


def test_pending_change_suppresses_notice():
    async def run():
        item = setup_notice()
        item.record["status"] = "pending"
        await item.notice.refresh_locked()
        item.channel.send.assert_not_awaited()

    asyncio.run(run())


def test_wrong_channel_is_rejected():
    async def run():
        item = setup_notice()
        item.channel.id = 999
        with pytest.raises(InterviewNotReady):
            await item.notice.refresh_locked()
        item.channel.send.assert_not_awaited()

    asyncio.run(run())


def test_resubmission_during_lookup_suppresses_notice():
    async def run():
        item = setup_notice()
        item.controller.load_request.side_effect = [None, object()]
        await item.notice.refresh_locked()
        item.channel.send.assert_not_awaited()

    asyncio.run(run())


def test_other_authors_marker_does_not_suppress_notice():
    async def run():
        item = setup_notice()
        embed = discord.Embed()
        embed.set_footer(text=f"wanbot:welcome:schedule-change:{CHANGE}")
        item.messages.append(SimpleNamespace(
            author=SimpleNamespace(id=999),
            webhook_id=None,
            embeds=[embed],
        ))
        await item.notice.refresh_locked()
        item.channel.send.assert_awaited_once()

    asyncio.run(run())
