import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord

from wanbot.bot import WanbotClient, build_registry
from wanbot.leave_log.feature import LeaveLogFeature
from wanbot.leave_log.models import DepartureSnapshot
import wanbot.leave_log.feature as feature_module


def event_case(enabled=True):
    user = Mock(spec=discord.User)
    user.id = 300
    user.name = "test_user"
    user.bot = False
    payload = SimpleNamespace(guild_id=100, user=user)
    feature = Mock(spec=LeaveLogFeature)
    feature.record = AsyncMock(return_value=True)
    ready = asyncio.Event()
    ready.set()
    client = SimpleNamespace(
        config=SimpleNamespace(
            guild_for=lambda guild_id: SimpleNamespace(
                feature_enabled=lambda name: enabled and name == "leave_log",
            ),
        ),
        is_closed=lambda: False,
        feature_available=lambda guild_id, name: True,
        feature_manager=SimpleNamespace(
            get_active=Mock(return_value=feature),
        ),
        _welcome_startup_complete=ready,
        _archive_departed_welcome=AsyncMock(),
    )
    return client, payload, feature


def test_event_saves_departure_and_runs_welcome_cleanup():
    async def run():
        client, payload, feature = event_case()
        await WanbotClient.on_raw_member_remove(client, payload)
        feature.record.assert_awaited_once()
        snapshot = feature.record.await_args.args[0]
        assert snapshot.guild_id == 100
        assert snapshot.user_id == 300
        assert snapshot.roles is None
        client._archive_departed_welcome.assert_awaited_once_with(payload)

    asyncio.run(run())


def test_capture_failure_does_not_skip_welcome():
    async def run():
        client, payload, feature = event_case()
        feature.record.side_effect = RuntimeError("save failed")
        await WanbotClient.on_raw_member_remove(client, payload)
        client._archive_departed_welcome.assert_awaited_once_with(payload)

    asyncio.run(run())


def test_disabled_leave_log_does_not_save():
    async def run():
        client, payload, feature = event_case(enabled=False)
        await WanbotClient.on_raw_member_remove(client, payload)
        feature.record.assert_not_awaited()
        client._archive_departed_welcome.assert_awaited_once_with(payload)

    asyncio.run(run())


def test_registry_contains_leave_log():
    registry = build_registry(Mock(), Mock())
    assert registry.get("leave_log") is not None


def test_feature_start_record_and_stop(monkeypatch):
    async def run():
        repository = SimpleNamespace(add=AsyncMock(), close=AsyncMock())
        results = SimpleNamespace(close=AsyncMock())
        processor = SimpleNamespace(tick=AsyncMock())
        monkeypatch.setattr(
            feature_module, "DepartureRepository",
            Mock(return_value=repository),
        )
        monkeypatch.setattr(
            feature_module, "DepartureResultRepository",
            Mock(return_value=results),
        )
        monkeypatch.setattr(
            feature_module, "DepartureDelivery", Mock(),
        )
        monkeypatch.setattr(
            feature_module, "DepartureProcessor",
            Mock(return_value=processor),
        )

        client = SimpleNamespace(feature_available=lambda *args: True)
        guild = SimpleNamespace(
            guild_id=100, feature_enabled=lambda name: name == "leave_log",
        )
        feature = LeaveLogFeature(client, None, guild)
        snapshot = DepartureSnapshot(
            event_id="a" * 32, guild_id=100, user_id=300,
            username="test_user", display_name=None, roles=None,
            joined_at=None,
            observed_at=datetime(2026, 10, 12, tzinfo=timezone.utc),
        )
        try:
            await feature.start()
            worker = feature._worker
            await feature.start()
            assert feature._worker is worker
            assert await feature.record(snapshot) is True
            repository.add.assert_awaited_once_with(snapshot)
        finally:
            await feature.stop()

        await feature.stop()
        assert worker.done()
        assert await feature.record(snapshot) is False
        repository.close.assert_awaited_once()
        results.close.assert_awaited_once()

    asyncio.run(run())
