import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from wanbot.bot import WanbotClient
from wanbot.config import parse_config
from wanbot.recruitment.feature import RecruitmentFeature
from wanbot.recruitment.sticky import (
    DebouncedPanelMove,
    is_own_panel_message,
)


def make_mover(move, *, delay=0.01, available=None):
    return DebouncedPanelMove(
        guild_id=100,
        move=move,
        is_available=available or Mock(return_value=True),
        delay=delay,
    )


def test_multiple_notifications_are_combined():
    async def run():
        move = AsyncMock()
        mover = make_mover(move)

        try:
            mover.notify()
            mover.notify()
            mover.notify()

            await asyncio.wait_for(mover._task, timeout=2)
            move.assert_awaited_once()
        finally:
            await mover.close()

    asyncio.run(run())


def test_post_during_move_triggers_another_move():
    async def run():
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def move():
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                await release.wait()

        mover = make_mover(move)

        try:
            mover.notify()
            await asyncio.wait_for(started.wait(), timeout=2)

            mover.notify()
            release.set()

            await asyncio.wait_for(mover._task, timeout=2)
            assert calls == 2
        finally:
            release.set()
            await mover.close()

    asyncio.run(run())


def test_close_cancels_pending_wait():
    async def run():
        move = AsyncMock()
        mover = make_mover(move, delay=60)

        mover.notify()
        await mover.close()
        mover.notify()
        await mover.close()

        move.assert_not_awaited()

    asyncio.run(run())


def test_close_waits_for_in_progress_move():
    async def run():
        started = asyncio.Event()
        release = asyncio.Event()
        finished = asyncio.Event()

        async def move():
            started.set()
            await release.wait()
            finished.set()

        mover = make_mover(move)
        mover.notify()
        await asyncio.wait_for(started.wait(), timeout=2)

        closing = asyncio.create_task(mover.close())
        try:
            await asyncio.sleep(0)
            assert not closing.done()

            release.set()
            await asyncio.wait_for(closing, timeout=2)
            assert finished.is_set()
        finally:
            release.set()
            await mover.close()

    asyncio.run(run())


def test_move_failure_is_logged_and_next_post_can_retry():
    async def run():
        move = AsyncMock(side_effect=[RuntimeError("Move failed"), None])
        mover = make_mover(move)

        try:
            with patch("wanbot.recruitment.sticky.log_event") as logger:
                mover.notify()
                await asyncio.wait_for(mover._task, timeout=2)
                logger.assert_called_once()

                mover.notify()
                await asyncio.wait_for(mover._task, timeout=2)

            assert move.await_count == 2
        finally:
            await mover.close()

    asyncio.run(run())


def test_unavailable_feature_does_not_move():
    async def run():
        move = AsyncMock()
        mover = make_mover(move, available=Mock(return_value=False))

        try:
            mover.notify()
            await asyncio.wait_for(mover._task, timeout=2)
            move.assert_not_awaited()
        finally:
            await mover.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("author_id", "custom_id", "expected"),
    [
        (300, "wanbot:recruitment:begin:100:200", True),
        (999, "wanbot:recruitment:begin:100:200", False),
        (300, None, False),
        (300, "another-button", False),
    ],
)
def test_only_own_panel_is_excluded(author_id, custom_id, expected):
    message = SimpleNamespace(
        author=SimpleNamespace(id=author_id),
        webhook_id=None,
        components=[
            SimpleNamespace(
                children=[SimpleNamespace(custom_id=custom_id)]
            )
        ],
    )

    assert is_own_panel_message(
        message,
        bot_user_id=300,
        guild_id=100,
        channel_id=200,
    ) is expected


def make_config():
    return parse_config({
        "DISCORD_TOKEN": "test-token",
        "PROD_GUILD_ID": "101",
        "TEST_GUILD_ID": "100",
        "TEST_ENABLED_FEATURES": "recruitment",
        "TEST_VALO_RECRUIT_CHANNEL_ID": "200",
        "TEST_GACHI_ROLE_ID": "400",
        "TEST_ENJOY_ROLE_ID": "500",
    })


@pytest.mark.parametrize("guild_id", [100, 101, 999, None])
def test_bot_routes_only_registered_recruitment_guild(guild_id):
    async def run():
        client = WanbotClient(make_config())
        feature = Mock(spec=RecruitmentFeature)
        feature.handle_message = AsyncMock()
        client.feature_available = Mock(return_value=True)
        client.feature_manager.get_active = Mock(return_value=feature)

        message = SimpleNamespace(
            guild=(
                SimpleNamespace(id=guild_id)
                if guild_id is not None
                else None
            ),
            channel=SimpleNamespace(id=200),
        )

        try:
            await client.on_message(message)
            if guild_id == 100:
                feature.handle_message.assert_awaited_once_with(message)
            else:
                feature.handle_message.assert_not_awaited()
        finally:
            await client.close()

    asyncio.run(run())


def test_recruitment_only_needs_message_events_not_content():
    async def run():
        client = WanbotClient(make_config())
        try:
            assert client.intents.guild_messages is True
            assert client.intents.message_content is False
        finally:
            await client.close()

    asyncio.run(run())
