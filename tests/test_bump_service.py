import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from wanbot.bump.repository import BumpRepository
from wanbot.bump.service import BumpService
from wanbot.config import parse_config
from wanbot.storage.json_store import StorageError


NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


async def sleep_forever(delay):
    await asyncio.Event().wait()


def setup(tmp_path, *, sleep=sleep_forever):
    config = parse_config({
        "DISCORD_TOKEN": "unit-test-token",
        "TEST_GUILD_ID": "100",
        "RUNTIME_DATA_DIR": str(tmp_path),
    })
    repository = BumpRepository(config, 100)
    renderer = AsyncMock()
    available = Mock(return_value=True)
    clock = SimpleNamespace(now=NOW)

    service = BumpService(
        guild_id=100,
        channel_id=200,
        disboard_bot_id=300,
        repository=repository,
        render_panel=renderer,
        is_available=available,
        clock=lambda: clock.now,
        sleep=sleep,
    )
    return service, repository, renderer, available, clock


def success_message(created_at=NOW):
    return SimpleNamespace(
        id=500,
        guild=SimpleNamespace(id=100),
        channel=SimpleNamespace(id=200),
        author=SimpleNamespace(id=300, bot=True),
        content="Bump done!",
        embeds=[],
        created_at=created_at,
    )


def test_start_restores_saved_deadline(tmp_path):
    async def scenario():
        service, repository, renderer, _, _ = setup(tmp_path)
        try:
            await repository.record_success(
                message_id=400,
                occurred_at=NOW - timedelta(hours=1),
            )
            await service.start()

            state, now, repost = renderer.call_args.args
            assert state.next_bump_at == NOW + timedelta(hours=1)
            assert not state.is_available(now)
            assert not repost
        finally:
            await service.close()

    asyncio.run(scenario())


def test_success_uses_post_time_and_requests_repost(tmp_path):
    async def scenario():
        service, repository, renderer, _, _ = setup(tmp_path)
        try:
            await service.start()
            renderer.reset_mock()

            posted_at = NOW - timedelta(minutes=10)
            assert await service.handle_message(success_message(posted_at))

            state = await repository.read()
            assert state.next_bump_at == posted_at + timedelta(hours=2)
            renderer.assert_awaited_once()
            assert renderer.call_args.args[2] is True
        finally:
            await service.close()

    asyncio.run(scenario())


def test_duplicate_success_does_not_repost(tmp_path):
    async def scenario():
        service, _, renderer, _, _ = setup(tmp_path)
        try:
            await service.start()
            await service.handle_message(success_message())
            renderer.reset_mock()

            assert not await service.handle_message(success_message())
            renderer.assert_not_awaited()
        finally:
            await service.close()

    asyncio.run(scenario())


def test_unrelated_message_does_not_change_state(tmp_path):
    async def scenario():
        service, repository, renderer, _, _ = setup(tmp_path)
        try:
            await service.start()
            renderer.reset_mock()
            message = success_message()
            message.author.id = 999

            assert not await service.handle_message(message)
            assert (await repository.read()).last_success_message_id is None
            renderer.assert_not_awaited()
        finally:
            await service.close()

    asyncio.run(scenario())


def test_background_task_updates_panel_at_deadline(tmp_path):
    async def scenario():
        ticks = 0
        finished = asyncio.Event()
        clock = None

        async def simulated_sleep(delay):
            nonlocal ticks
            ticks += 1
            if ticks == 1:
                clock.now = NOW + timedelta(seconds=30)
                return
            await asyncio.Event().wait()

        service, repository, renderer, _, clock = setup(
            tmp_path, sleep=simulated_sleep
        )

        async def render(state, now, repost):
            if state.is_available(now):
                finished.set()

        renderer.side_effect = render

        try:
            await repository.record_success(
                message_id=400,
                occurred_at=NOW,
                cooldown_seconds=30,
            )
            await service.start()
            await asyncio.wait_for(finished.wait(), timeout=2)

            assert renderer.await_count == 2
            assert renderer.call_args.args[2] is False
            assert (await repository.read()).last_success_message_id == 400
        finally:
            await service.close()

    asyncio.run(scenario())


def test_background_display_failure_is_retried(tmp_path, monkeypatch):
    monkeypatch.setattr("wanbot.bump.service.log_event", Mock())

    async def scenario():
        ticks = 0
        calls = 0
        finished = asyncio.Event()
        clock = None

        async def simulated_sleep(delay):
            nonlocal ticks
            ticks += 1
            if ticks <= 2:
                clock.now += timedelta(seconds=30)
                return
            await asyncio.Event().wait()

        service, repository, renderer, _, clock = setup(
            tmp_path, sleep=simulated_sleep
        )

        async def render(state, now, repost):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("simulated display failure")
            if calls == 3:
                finished.set()

        renderer.side_effect = render

        try:
            await repository.record_success(
                message_id=400,
                occurred_at=NOW,
                cooldown_seconds=30,
            )
            await service.start()
            await asyncio.wait_for(finished.wait(), timeout=2)

            assert calls == 3
            state = await repository.read()
            assert state.next_bump_at == NOW + timedelta(seconds=30)
        finally:
            await service.close()

    asyncio.run(scenario())


def test_save_failure_does_not_update_panel(tmp_path, monkeypatch):
    async def scenario():
        service, repository, renderer, _, _ = setup(tmp_path)
        try:
            await service.start()
            renderer.reset_mock()
            monkeypatch.setattr(
                repository,
                "record_success",
                AsyncMock(side_effect=StorageError("simulated failure")),
            )

            with pytest.raises(StorageError):
                await service.handle_message(success_message())

            renderer.assert_not_awaited()
        finally:
            await service.close()

    asyncio.run(scenario())


def test_disabled_feature_ignores_success(tmp_path):
    async def scenario():
        service, repository, renderer, available, _ = setup(tmp_path)
        try:
            await service.start()
            renderer.reset_mock()
            available.return_value = False

            assert not await service.handle_message(success_message())
            assert (await repository.read()).last_success_message_id is None
            renderer.assert_not_awaited()
        finally:
            await service.close()

    asyncio.run(scenario())


def test_stopped_service_does_not_process_messages(tmp_path):
    async def scenario():
        service, _, renderer, _, _ = setup(tmp_path)
        await service.start()
        await service.close()
        await service.close()
        renderer.reset_mock()

        assert not await service.handle_message(success_message())
        renderer.assert_not_awaited()

    asyncio.run(scenario())
