import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from wanbot.config import parse_config
from wanbot.welcome.repository import (
    WelcomeConflict,
    WelcomeDataError,
    WelcomeRepository,
)


NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def make_config(tmp_path):
    return parse_config({
        "DISCORD_TOKEN": "test-token",
        "PROD_GUILD_ID": "101",
        "TEST_GUILD_ID": "100",
        "RUNTIME_DATA_DIR": str(tmp_path / "runtime"),
    })


def test_begin_creates_preparing_session(tmp_path):
    async def run():
        repository = WelcomeRepository(make_config(tmp_path), 100)
        try:
            session = await repository.begin(300, now=NOW)

            assert session.user_id == 300
            assert session.state == "preparing"
            assert session.channel_id is None
            assert session.created_at == NOW
            assert await repository.get_active(300) == session
        finally:
            await repository.close()

    asyncio.run(run())


def test_reopen_preserves_session_and_original_time(tmp_path):
    async def run():
        config = make_config(tmp_path)
        repository = WelcomeRepository(config, 100)
        try:
            original = await repository.begin(300, now=NOW)
        finally:
            await repository.close()

        reopened = WelcomeRepository(config, 100)
        try:
            repeated = await reopened.begin(
                300,
                now=NOW + timedelta(hours=1),
            )
            assert repeated == original
        finally:
            await reopened.close()

    asyncio.run(run())


def test_concurrent_begin_creates_only_one_session(tmp_path):
    async def run():
        config = make_config(tmp_path)
        first = WelcomeRepository(config, 100)
        second = WelcomeRepository(config, 100)

        try:
            left, right = await asyncio.gather(
                first.begin(300, now=NOW),
                second.begin(300, now=NOW),
            )

            assert left.session_id == right.session_id
            assert len(await first.history(300)) == 1
        finally:
            await first.close()
            await second.close()

    asyncio.run(run())


def test_attach_channel_is_saved_and_idempotent(tmp_path):
    async def run():
        repository = WelcomeRepository(make_config(tmp_path), 100)
        try:
            session = await repository.begin(300, now=NOW)
            attached = await repository.attach_channel(
                session.session_id,
                400,
                now=NOW,
            )
            repeated = await repository.attach_channel(
                session.session_id,
                400,
                now=NOW + timedelta(minutes=1),
            )

            assert attached.state == "active"
            assert attached.channel_id == 400
            assert repeated == attached
            assert await repository.get_active(300) == attached
        finally:
            await repository.close()

    asyncio.run(run())


def test_active_channel_cannot_be_replaced(tmp_path):
    async def run():
        repository = WelcomeRepository(make_config(tmp_path), 100)
        try:
            session = await repository.begin(300, now=NOW)
            await repository.attach_channel(session.session_id, 400, now=NOW)

            with pytest.raises(WelcomeConflict):
                await repository.attach_channel(
                    session.session_id,
                    401,
                    now=NOW,
                )

            assert (await repository.get_active(300)).channel_id == 400
        finally:
            await repository.close()

    asyncio.run(run())


def test_rejoin_creates_new_session_and_keeps_history(tmp_path):
    async def run():
        repository = WelcomeRepository(make_config(tmp_path), 100)
        try:
            previous = await repository.begin(300, now=NOW)
            await repository.attach_channel(
                previous.session_id,
                400,
                now=NOW,
            )
            await repository.close_session(
                previous.session_id,
                reason="member_left",
                now=NOW + timedelta(minutes=1),
            )

            assert await repository.get_active(300) is None

            current = await repository.begin(
                300,
                now=NOW + timedelta(minutes=2),
            )
            history = await repository.history(300)

            assert current.session_id != previous.session_id
            assert len(history) == 2
            assert history[0].state == "closed"
            assert history[0].channel_id == 400
            assert history[1] == current
        finally:
            await repository.close()

    asyncio.run(run())


def test_guilds_have_separate_sessions(tmp_path):
    async def run():
        config = make_config(tmp_path)
        test = WelcomeRepository(config, 100)
        prod = WelcomeRepository(config, 101)
        try:
            await test.begin(300, now=NOW)
            assert await prod.get_active(300) is None
            assert await prod.history(300) == ()
        finally:
            await test.close()
            await prod.close()

    asyncio.run(run())


def test_old_session_cannot_attach_channel_after_rejoin(tmp_path):
    async def run():
        repository = WelcomeRepository(make_config(tmp_path), 100)
        try:
            previous = await repository.begin(300, now=NOW)
            await repository.close_session(
                previous.session_id,
                reason="member_left",
                now=NOW,
            )
            current = await repository.begin(300, now=NOW)

            with pytest.raises(WelcomeConflict):
                await repository.attach_channel(
                    previous.session_id,
                    400,
                    now=NOW,
                )

            assert await repository.get_active(300) == current
        finally:
            await repository.close()

    asyncio.run(run())


@pytest.mark.parametrize("user_id", [0, True, 1 << 64])
def test_invalid_user_id_is_rejected(tmp_path, user_id):
    async def run():
        repository = WelcomeRepository(make_config(tmp_path), 100)
        try:
            with pytest.raises(ValueError):
                await repository.begin(user_id, now=NOW)
        finally:
            await repository.close()

    asyncio.run(run())


def test_corrupt_data_is_not_overwritten(tmp_path):
    async def run():
        repository = WelcomeRepository(make_config(tmp_path), 100)
        try:
            def corrupt(data):
                return {"state_version": 999, "sessions": {}}

            await repository._store.update(corrupt)

            with pytest.raises(WelcomeDataError):
                await repository.begin(300, now=NOW)

            assert await repository._store.read() == {
                "state_version": 999,
                "sessions": {},
            }
        finally:
            await repository.close()

    asyncio.run(run())


def test_backdated_transition_is_rejected(tmp_path):
    async def run():
        repository = WelcomeRepository(make_config(tmp_path), 100)
        try:
            session = await repository.begin(300, now=NOW)

            with pytest.raises(WelcomeConflict):
                await repository.attach_channel(
                    session.session_id,
                    400,
                    now=NOW - timedelta(seconds=1),
                )

            assert await repository.get_active(300) == session
        finally:
            await repository.close()

    asyncio.run(run())
