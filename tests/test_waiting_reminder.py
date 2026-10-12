"""48時間リマインドの投稿と再試行。"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from wanbot.welcome.waiting_reminder import WaitingReminder


SID = "a" * 32
EPOCH = "b" * 32
NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


def setup():
    record = {
        "epoch": EPOCH,
        "reason": "introduction",
        "started_at": NOW.isoformat(),
    }
    messages = []
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 500
    channel.guild = SimpleNamespace(id=100)
    channel.topic = f"wanbot:welcome:100:{SID}"

    async def history(*, limit):
        for message in list(messages):
            yield message

    async def send(**kwargs):
        message = SimpleNamespace(
            author=SimpleNamespace(id=999),
            webhook_id=None,
            embeds=[kwargs["embed"]],
        )
        messages.append(message)
        return message

    channel.history = history
    channel.send = AsyncMock(side_effect=send)

    controller = SimpleNamespace(
        _context=SimpleNamespace(
            guild_id=100, user_id=300, channel_id=500, session_id=SID
        ),
        _client=SimpleNamespace(
            user=SimpleNamespace(id=999),
            fetch_channel=AsyncMock(return_value=channel),
        ),
        _session=AsyncMock(),
        _clock=lambda: NOW + timedelta(hours=48),
        _lock=asyncio.Lock(),
    )
    repository = SimpleNamespace(mark_reminded=AsyncMock())
    monitor = SimpleNamespace(
        _controller=controller,
        _repository=repository,
        available=lambda: True,
        candidate_locked=AsyncMock(return_value=(record, "remind")),
    )
    return WaitingReminder(monitor=monitor), monitor, channel, messages, record


def test_sends_applicant_mention_and_records_success():
    async def scenario():
        reminder, monitor, channel, messages, record = setup()
        await reminder.refresh()

        channel.send.assert_awaited_once()
        options = channel.send.call_args.kwargs
        assert options["content"] == "<@300>"
        assert options["allowed_mentions"].everyone is False
        assert options["allowed_mentions"].roles is False
        assert [
            user.id for user in options["allowed_mentions"].users
        ] == [300]
        assert "管理者へキックの承認を依頼" in options["embed"].description
        monitor._repository.mark_reminded.assert_awaited_once_with(
            SID,
            expected_epoch=EPOCH,
            now=NOW + timedelta(hours=48),
        )

    asyncio.run(scenario())


def test_retry_after_save_failure_reuses_existing_post():
    async def scenario():
        reminder, monitor, channel, messages, record = setup()
        monitor._repository.mark_reminded.side_effect = RuntimeError(
            "storage unavailable"
        )
        with pytest.raises(RuntimeError):
            await reminder.refresh()

        assert len(messages) == 1
        monitor._repository.mark_reminded.side_effect = None

        # 再起動を想定し、送信処理のオブジェクトを作り直す。
        restored = WaitingReminder(monitor=monitor)
        await restored.refresh()

        channel.send.assert_awaited_once()
        assert monitor._repository.mark_reminded.await_count == 2

    asyncio.run(scenario())


def test_send_failure_does_not_mark_reminded():
    async def scenario():
        reminder, monitor, channel, messages, record = setup()
        channel.send.side_effect = RuntimeError("send failed")

        with pytest.raises(RuntimeError):
            await reminder.refresh()

        monitor._repository.mark_reminded.assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", [None, "review"])
def test_no_reminder_before_due_or_after_72_hours(action):
    async def scenario():
        reminder, monitor, channel, messages, record = setup()
        monitor.candidate_locked.return_value = (record, action)

        await reminder.refresh()

        channel.send.assert_not_awaited()
        monitor._repository.mark_reminded.assert_not_awaited()

    asyncio.run(scenario())


def test_progress_before_send_cancels_old_reminder():
    async def scenario():
        reminder, monitor, channel, messages, record = setup()
        monitor.candidate_locked.side_effect = [
            (record, "remind"),
            ({**record, "epoch": "c" * 32}, None),
        ]

        await reminder.refresh()

        channel.send.assert_not_awaited()
        monitor._repository.mark_reminded.assert_not_awaited()

    asyncio.run(scenario())


def test_wrong_welcome_channel_is_rejected():
    async def scenario():
        reminder, monitor, channel, messages, record = setup()
        channel.topic = "different-welcome"

        with pytest.raises(ValueError):
            await reminder.refresh()

        channel.send.assert_not_awaited()
        monitor._repository.mark_reminded.assert_not_awaited()

    asyncio.run(scenario())
