import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from wanbot.welcome.admission_service import AdmissionService


def setup():
    record = {
        "session_id": "a" * 32,
        "proposal_id": "b" * 32,
        "guild_id": 100,
        "user_id": 300,
        "channel_id": 500,
        "role_ids": [10, 20],
        "phase": "approved",
    }
    session = SimpleNamespace(
        session_id=record["session_id"],
        channel_id=500,
        state="active",
        closed_reason=None,
    )
    archiving = SimpleNamespace(
        session_id=session.session_id,
        channel_id=500,
        state="archiving",
        closed_reason="completed",
    )
    repository = SimpleNamespace(
        get=AsyncMock(return_value=record),
        mark_roles_verified=AsyncMock(),
        mark_completed=AsyncMock(),
    )
    welcome = SimpleNamespace(
        history=AsyncMock(return_value=(session,)),
        get_active=AsyncMock(return_value=session),
        begin_archive=AsyncMock(return_value=archiving),
        record_archive_channel=AsyncMock(),
        close_session=AsyncMock(),
    )
    archive = SimpleNamespace(archive=AsyncMock(return_value=500))
    service = AdmissionService(
        client=object(),
        repository=repository,
        welcome_repository=welcome,
        archive=archive,
        lock=asyncio.Lock(),
    )
    return service, record, repository, welcome, archive, session


def test_role_failure_does_not_start_archive():
    async def scenario():
        service, record, repository, welcome, archive, _ = setup()
        with patch(
            "wanbot.welcome.admission_service.grant_admission_roles",
            new=AsyncMock(side_effect=RuntimeError("role failure")),
        ):
            with pytest.raises(RuntimeError):
                await service.finish_locked(record)
        welcome.begin_archive.assert_not_awaited()
        archive.archive.assert_not_awaited()
        repository.mark_completed.assert_not_awaited()

    asyncio.run(scenario())


def test_success_follows_required_order():
    async def scenario():
        service, record, repository, welcome, archive, _ = setup()
        events = []

        async def grant(**kwargs):
            events.append("roles")

        async def verified(*args):
            events.append("verified")

        async def move(*args):
            events.append("archive")
            return 500

        async def close(*args, **kwargs):
            events.append("close")

        async def complete(*args):
            events.append("completed")

        repository.mark_roles_verified.side_effect = verified
        archive.archive.side_effect = move
        welcome.close_session.side_effect = close
        repository.mark_completed.side_effect = complete

        with patch(
            "wanbot.welcome.admission_service.grant_admission_roles",
            new=grant,
        ):
            await service.finish_locked(record)

        assert events == ["roles", "verified", "archive", "close", "completed"]

    asyncio.run(scenario())


def test_archive_failure_does_not_mark_completed():
    async def scenario():
        service, record, repository, welcome, archive, _ = setup()
        archive.archive.side_effect = RuntimeError("archive failure")
        with patch(
            "wanbot.welcome.admission_service.grant_admission_roles",
            new=AsyncMock(),
        ):
            with pytest.raises(RuntimeError):
                await service.finish_locked(record)
        repository.mark_roles_verified.assert_awaited_once()
        welcome.close_session.assert_not_awaited()
        repository.mark_completed.assert_not_awaited()

    asyncio.run(scenario())


def test_closed_completed_session_finishes_record_only():
    async def scenario():
        service, record, repository, _, archive, session = setup()
        record["phase"] = "roles_verified"
        session.state = "closed"
        session.closed_reason = "completed"
        with patch(
            "wanbot.welcome.admission_service.grant_admission_roles",
            new=AsyncMock(),
        ) as grant:
            await service.finish_locked(record)
        grant.assert_not_awaited()
        archive.archive.assert_not_awaited()
        repository.mark_completed.assert_awaited_once()

    asyncio.run(scenario())
