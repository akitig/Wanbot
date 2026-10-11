import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from wanbot.config import parse_config
from wanbot.welcome.repository import WelcomeConflict, WelcomeRepository


NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def make_config(tmp_path):
    return parse_config({
        "DISCORD_TOKEN": "test-token",
        "PROD_GUILD_ID": "101",
        "TEST_GUILD_ID": "100",
        "RUNTIME_DATA_DIR": str(tmp_path / "runtime"),
    })


def test_pending_archive_survives_reopening(tmp_path):
    async def run():
        config = make_config(tmp_path)
        repository = WelcomeRepository(config, 100)
        try:
            session = await repository.begin(300, now=NOW)
            await repository.attach_channel(
                session.session_id, 500, now=NOW
            )
            pending = await repository.begin_archive(
                session.session_id,
                reason="member_left",
                now=NOW + timedelta(minutes=1),
            )
            assert pending.state == "archiving"
            assert pending.channel_id == 500
            assert pending.closed_reason == "member_left"
        finally:
            await repository.close()

        reopened = WelcomeRepository(config, 100)
        try:
            assert await reopened.pending_archives() == (pending,)
            assert await reopened.get_active(300) == pending
        finally:
            await reopened.close()

    asyncio.run(run())


def test_rejoin_is_blocked_until_archive_finishes(tmp_path):
    async def run():
        repository = WelcomeRepository(make_config(tmp_path), 100)
        try:
            old = await repository.begin(300, now=NOW)
            await repository.attach_channel(old.session_id, 500, now=NOW)
            await repository.begin_archive(
                old.session_id, reason="member_left", now=NOW
            )

            with pytest.raises(WelcomeConflict):
                await repository.begin(300, now=NOW)

            assert len(await repository.history(300)) == 1

            await repository.close_session(
                old.session_id, reason="member_left", now=NOW
            )
            new = await repository.begin(
                300, now=NOW + timedelta(minutes=1)
            )

            assert new.session_id != old.session_id
            assert new.state == "preparing"
            assert await repository.pending_archives() == ()

            history = await repository.history(300)
            assert len(history) == 2
            assert history[0].state == "closed"
            assert history[0].channel_id == 500
        finally:
            await repository.close()

    asyncio.run(run())


def test_duplicate_archive_request_preserves_timestamp(tmp_path):
    async def run():
        repository = WelcomeRepository(make_config(tmp_path), 100)
        try:
            session = await repository.begin(300, now=NOW)
            first = await repository.begin_archive(
                session.session_id, reason="member_left", now=NOW
            )
            repeated = await repository.begin_archive(
                session.session_id,
                reason="member_left",
                now=NOW + timedelta(hours=1),
            )

            assert repeated == first
            assert await repository.pending_archives() == (first,)
        finally:
            await repository.close()

    asyncio.run(run())


def test_archive_reason_cannot_change(tmp_path):
    async def run():
        repository = WelcomeRepository(make_config(tmp_path), 100)
        try:
            session = await repository.begin(300, now=NOW)
            pending = await repository.begin_archive(
                session.session_id, reason="member_left", now=NOW
            )

            with pytest.raises(WelcomeConflict):
                await repository.begin_archive(
                    session.session_id, reason="completed", now=NOW
                )

            with pytest.raises(WelcomeConflict):
                await repository.close_session(
                    session.session_id, reason="completed", now=NOW
                )

            assert await repository.pending_archives() == (pending,)
        finally:
            await repository.close()

    asyncio.run(run())


def test_preparing_session_can_wait_for_archive(tmp_path):
    async def run():
        repository = WelcomeRepository(make_config(tmp_path), 100)
        try:
            session = await repository.begin(300, now=NOW)
            pending = await repository.begin_archive(
                session.session_id, reason="member_left", now=NOW
            )

            assert pending.channel_id is None
            assert await repository.pending_archives() == (pending,)

            with pytest.raises(WelcomeConflict):
                await repository.attach_channel(
                    session.session_id, 500, now=NOW
                )
        finally:
            await repository.close()

    asyncio.run(run())


def test_closed_session_is_not_reopened_by_archive_request(tmp_path):
    async def run():
        repository = WelcomeRepository(make_config(tmp_path), 100)
        try:
            session = await repository.begin(300, now=NOW)
            closed = await repository.close_session(
                session.session_id, reason="member_left", now=NOW
            )
            repeated = await repository.begin_archive(
                session.session_id,
                reason="member_left",
                now=NOW + timedelta(minutes=1),
            )

            assert repeated == closed
            assert await repository.pending_archives() == ()
        finally:
            await repository.close()

    asyncio.run(run())


def test_archive_rejects_backdated_transition(tmp_path):
    async def run():
        repository = WelcomeRepository(make_config(tmp_path), 100)
        try:
            session = await repository.begin(300, now=NOW)

            with pytest.raises(WelcomeConflict):
                await repository.begin_archive(
                    session.session_id,
                    reason="member_left",
                    now=NOW - timedelta(seconds=1),
                )

            assert await repository.get_active(300) == session
            assert await repository.pending_archives() == ()
        finally:
            await repository.close()

    asyncio.run(run())
