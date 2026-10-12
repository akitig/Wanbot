import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from wanbot.config import parse_config
from wanbot.welcome.interview_repository import (
    InterviewRequestConflict,
    InterviewRequestRepository,
)


NOW = datetime(2026, 10, 11, 7, 0, tzinfo=timezone.utc)
DATES = (
    "2026-10-12 18:00",
    "2026-10-13 19:00",
    "2026-10-14 20:00",
)


def make_config(tmp_path):
    return parse_config(
        {
            "DISCORD_TOKEN": "test-token",
            "TEST_GUILD_ID": "100",
            "PROD_GUILD_ID": "101",
            "RUNTIME_DATA_DIR": str(tmp_path / "runtime"),
        },
        base_dir=tmp_path,
    )


def make_session(session_id="a" * 32):
    return SimpleNamespace(
        session_id=session_id,
        user_id=300,
        channel_id=500,
        state="active",
    )


def test_confirmed_dates_survive_reopening(tmp_path):
    async def scenario():
        config = make_config(tmp_path)
        repository = InterviewRequestRepository(config, 100)
        session = make_session()
        try:
            saved = await repository.confirm(session, DATES, now=NOW)
        finally:
            await repository.close()

        reopened = InterviewRequestRepository(config, 100)
        try:
            restored = await reopened.get(session.session_id)
            assert restored == saved
            assert restored.user_id == 300
            assert restored.channel_id == 500
            assert restored.confirmed_at == NOW
            assert restored.preferences[0].hour == 18
        finally:
            await reopened.close()

    asyncio.run(scenario())


def test_confirmed_dates_cannot_be_overwritten(tmp_path):
    async def scenario():
        repository = InterviewRequestRepository(make_config(tmp_path), 100)
        session = make_session()
        try:
            original = await repository.confirm(session, DATES, now=NOW)
            changed = (
                "2026-10-15 18:00",
                "2026-10-16 19:00",
                "2026-10-17 20:00",
            )
            with pytest.raises(InterviewRequestConflict):
                await repository.confirm(session, changed, now=NOW)
            assert await repository.get(session.session_id) == original
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_concurrent_confirmation_accepts_only_one(tmp_path):
    async def scenario():
        config = make_config(tmp_path)
        first = InterviewRequestRepository(config, 100)
        second = InterviewRequestRepository(config, 100)
        try:
            results = await asyncio.gather(
                first.confirm(make_session(), DATES, now=NOW),
                second.confirm(make_session(), DATES, now=NOW),
                return_exceptions=True,
            )
            assert sum(
                isinstance(result, InterviewRequestConflict)
                for result in results
            ) == 1
            assert await first.get("a" * 32) is not None
        finally:
            await first.close()
            await second.close()

    asyncio.run(scenario())


def test_rejoined_member_has_separate_request(tmp_path):
    async def scenario():
        repository = InterviewRequestRepository(make_config(tmp_path), 100)
        try:
            await repository.confirm(make_session(), DATES, now=NOW)
            assert await repository.get("b" * 32) is None
            await repository.confirm(
                make_session("b" * 32), DATES, now=NOW
            )
            assert await repository.get("a" * 32) is not None
            assert await repository.get("b" * 32) is not None
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_guilds_have_separate_requests(tmp_path):
    async def scenario():
        config = make_config(tmp_path)
        test = InterviewRequestRepository(config, 100)
        prod = InterviewRequestRepository(config, 101)
        try:
            await test.confirm(make_session(), DATES, now=NOW)
            assert await prod.get("a" * 32) is None
        finally:
            await test.close()
            await prod.close()

    asyncio.run(scenario())


def test_closed_welcome_cannot_confirm(tmp_path):
    async def scenario():
        repository = InterviewRequestRepository(make_config(tmp_path), 100)
        session = make_session()
        session.state = "closed"
        try:
            with pytest.raises(InterviewRequestConflict):
                await repository.confirm(session, DATES, now=NOW)
            assert await repository.get(session.session_id) is None
        finally:
            await repository.close()

    asyncio.run(scenario())
