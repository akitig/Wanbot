import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from wanbot.bot import WanbotClient
from wanbot.welcome.feature import WelcomeFeature
import wanbot.welcome.feature as feature_module


def event_case():
    feature = Mock(spec=WelcomeFeature)
    feature.member_left = AsyncMock(return_value=None)
    guild = SimpleNamespace(feature_enabled=lambda name: True)
    ready = asyncio.Event()
    ready.set()

    client = SimpleNamespace(
        config=SimpleNamespace(
            guild_for=lambda guild_id: guild if guild_id == 100 else None
        ),
        is_closed=Mock(return_value=False),
        feature_available=Mock(return_value=True),
        feature_manager=SimpleNamespace(
            get_active=Mock(return_value=feature)
        ),
        _welcome_startup_complete=ready,
    )
    payload = SimpleNamespace(
        guild_id=100,
        user=SimpleNamespace(id=300, bot=False),
    )
    # このテストではWelcomeの退出処理だけを有効にする。
    guild.feature_enabled = lambda name: name == "welcome"

    # 仮のBotにも、分離した本物のWelcome終了処理を接続する。
    client._archive_departed_welcome = (
        WanbotClient._archive_departed_welcome.__get__(client, WanbotClient)
    )
    return client, payload, feature, guild


def test_raw_leave_works_without_cached_member():
    async def run():
        client, payload, feature, _ = event_case()

        await WanbotClient.on_raw_member_remove(client, payload)

        feature.member_left.assert_awaited_once()
        member = feature.member_left.call_args.args[0]
        assert member.id == 300
        assert member.guild.id == 100
        assert member.bot is False

    asyncio.run(run())


@pytest.mark.parametrize(
    "case",
    ["bot", "unregistered", "disabled", "closed", "unavailable"],
)
def test_raw_leave_ignores_ineligible_event(case):
    async def run():
        client, payload, feature, guild = event_case()

        if case == "bot":
            payload.user.bot = True
        elif case == "unregistered":
            payload.guild_id = 999
        elif case == "disabled":
            guild.feature_enabled = lambda name: False
        elif case == "closed":
            client.is_closed.return_value = True
        else:
            client.feature_available.return_value = False

        await WanbotClient.on_raw_member_remove(client, payload)

        feature.member_left.assert_not_awaited()

    asyncio.run(run())


def test_raw_leave_failure_does_not_escape_handler():
    async def run():
        client, payload, feature, _ = event_case()
        feature.member_left.side_effect = RuntimeError("archive failed")

        await WanbotClient.on_raw_member_remove(client, payload)

        feature.member_left.assert_awaited_once()

    asyncio.run(run())


def test_raw_leave_waits_for_startup():
    async def run():
        client, payload, feature, _ = event_case()
        client._welcome_startup_complete.clear()

        task = asyncio.create_task(
            WanbotClient.on_raw_member_remove(client, payload)
        )
        try:
            await asyncio.sleep(0)
            feature.member_left.assert_not_awaited()

            client._welcome_startup_complete.set()
            await asyncio.wait_for(task, timeout=2)

            feature.member_left.assert_awaited_once()
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def feature_case(monkeypatch):
    repository = SimpleNamespace(
        close=AsyncMock(),
        open_sessions=AsyncMock(return_value=()),
    )
    channels = SimpleNamespace(ensure=AsyncMock())
    archive = SimpleNamespace(archive=AsyncMock())
    lifecycle = SimpleNamespace(
        operation_lock=asyncio.Lock(),
        ensure=AsyncMock(),
        member_left=AsyncMock(),
        resume_pending=AsyncMock(return_value=(0, 0)),
        reconcile_members=AsyncMock(return_value=(0, 0)),
    )
    client = Mock()
    client.feature_available.return_value = True
    guild = SimpleNamespace(
        guild_id=100,
        feature_enabled=lambda name: True,
    )

    monkeypatch.setattr(
        feature_module, "WelcomeRepository",
        Mock(return_value=repository),
    )
    monkeypatch.setattr(
        feature_module, "WelcomeChannels",
        Mock(return_value=channels),
    )
    monkeypatch.setattr(
        feature_module, "WelcomeArchiveChannel",
        Mock(return_value=archive),
    )
    monkeypatch.setattr(
        feature_module, "WelcomeLifecycle",
        Mock(return_value=lifecycle),
    )
    monkeypatch.setattr(
        feature_module, "register_welcome_command", Mock(),
    )

    monkeypatch.setattr(
        feature_module, "QuestionnaireRepository",
        Mock(return_value=SimpleNamespace(close=AsyncMock())),
    )
    monkeypatch.setattr(
        feature_module, "QuestionnairePanelRepository",
        Mock(return_value=SimpleNamespace(close=AsyncMock())),
    )
    monkeypatch.setattr(
        feature_module, "WelcomeQuestionnaires",
        Mock(return_value=SimpleNamespace(
            ensure=AsyncMock(),
            refresh=AsyncMock(),
            close=AsyncMock(),
        )),
    )

    monkeypatch.setattr(
        feature_module, "QuestionnaireResultRepository",
        Mock(return_value=SimpleNamespace(close=AsyncMock())),
    )

    # ルール確認処理の初期化に必要な、テスト専用の設定。
    guild.ids = {
        "RULES_CHANNEL_ID": 500,
        "RULES_MESSAGE_ID": 600,
    }
    guild.rules_emoji = "✅"
    from pathlib import Path
    from tempfile import TemporaryDirectory

    # テスト中だけ使う保存先。終了時に片付ける。
    storage_directory = TemporaryDirectory()
    config = Mock()
    config.runtime_data_dir = Path(storage_directory.name)
    # 実際のGuildConfigと同じ、通常の本人期限を設定する。
    guild.environment = "test"
    guild.welcome_reminder_seconds = 48 * 60 * 60
    guild.welcome_timeout_seconds = 72 * 60 * 60
    feature = WelcomeFeature(client, config, guild)
    feature._test_storage_directory = storage_directory
    return feature, lifecycle, repository


def test_feature_delegates_leave(monkeypatch):
    async def run():
        feature, lifecycle, _ = feature_case(monkeypatch)
        member = object()
        try:
            await feature.start()
            await feature.member_left(member)
            lifecycle.member_left.assert_awaited_once_with(member)
        finally:
            await feature.stop()

    asyncio.run(run())


def test_worker_retries_after_failure(monkeypatch):
    async def run():
        feature, lifecycle, _ = feature_case(monkeypatch)
        monkeypatch.setattr(feature_module, "ARCHIVE_RETRY_SECONDS", 0.01)
        recovered = asyncio.Event()
        calls = 0

        async def resume():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("temporary failure")
            recovered.set()
            return 1, 0

        lifecycle.resume_pending.side_effect = resume

        try:
            await feature.start()
            await asyncio.wait_for(recovered.wait(), timeout=2)
            assert calls >= 2
        finally:
            await feature.stop()

        assert feature._worker.done()

    asyncio.run(run())


def test_stop_waits_for_running_archive(monkeypatch):
    async def run():
        feature, lifecycle, repository = feature_case(monkeypatch)
        started = asyncio.Event()
        release = asyncio.Event()

        async def archive(member):
            started.set()
            await release.wait()
            return "archived"

        lifecycle.member_left.side_effect = archive
        await feature.start()
        operation = asyncio.create_task(feature.member_left(object()))
        stopping = None

        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            stopping = asyncio.create_task(feature.stop())
            await asyncio.sleep(0)

            repository.close.assert_not_awaited()

            release.set()
            assert await asyncio.wait_for(operation, timeout=2) == "archived"
            await asyncio.wait_for(stopping, timeout=2)
            repository.close.assert_awaited_once()
        finally:
            release.set()
            await asyncio.gather(operation, return_exceptions=True)
            if stopping is not None:
                await stopping
            else:
                await feature.stop()

    asyncio.run(run())


def test_worker_retries_membership_check_until_success(monkeypatch):
    async def run():
        feature, lifecycle, _ = feature_case(monkeypatch)
        monkeypatch.setattr(feature_module, "ARCHIVE_RETRY_SECONDS", 0.01)
        lifecycle.reconcile_members.side_effect = [(0, 1), (1, 0)]

        async def wait_until_completed():
            while feature._reconcile_needed:
                await asyncio.sleep(0.001)

        try:
            await feature.start()
            await asyncio.wait_for(wait_until_completed(), timeout=2)

            assert lifecycle.reconcile_members.await_count == 2

            # 成功後は、同じ起動中に全員の在籍確認を繰り返さない。
            await asyncio.sleep(0.04)
            assert lifecycle.reconcile_members.await_count == 2
        finally:
            await feature.stop()

    asyncio.run(run())
