import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from wanbot.bump.repository import BumpRepository, BumpState
from wanbot.config import parse_config
from wanbot.storage.json_store import GuildJsonStore, StorageError


NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def make_config(tmp_path):
    return parse_config({
        "DISCORD_TOKEN": "unit-test-token",
        "PROD_GUILD_ID": "100",
        "TEST_GUILD_ID": "200",
        "RUNTIME_DATA_DIR": str(tmp_path),
    })


async def record(repository, message_id=500, occurred_at=NOW, **kwargs):
    return await repository.record_success(
        message_id=message_id,
        occurred_at=occurred_at,
        **kwargs,
    )


def test_missing_state_is_available(tmp_path):
    async def scenario():
        repository = BumpRepository(make_config(tmp_path), 100)
        try:
            state = await repository.read()
            assert state == BumpState()
            assert state.is_available(NOW)
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_state_survives_reopening_without_extending_deadline(tmp_path):
    config = make_config(tmp_path)

    async def scenario():
        first = BumpRepository(config, 100)
        try:
            await first.set_panel_message_id(700)
            assert await record(first)
        finally:
            await first.close()

        second = BumpRepository(config, 100)
        try:
            state = await second.read()
            assert state.panel_message_id == 700
            assert state.last_success_message_id == 500
            assert state.last_bumped_at == NOW
            assert state.next_bump_at == NOW + timedelta(hours=2)
            assert not state.is_available(NOW + timedelta(hours=1))
        finally:
            await second.close()

    asyncio.run(scenario())


def test_guilds_have_separate_state(tmp_path):
    config = make_config(tmp_path)

    async def scenario():
        prod = BumpRepository(config, 100)
        test = BumpRepository(config, 200)
        try:
            await record(prod)

            assert not (await prod.read()).is_available(NOW)
            assert (await test.read()).is_available(NOW)
        finally:
            await prod.close()
            await test.close()

    asyncio.run(scenario())


def test_panel_update_preserves_cooldown(tmp_path):
    async def scenario():
        repository = BumpRepository(make_config(tmp_path), 100)
        try:
            await record(repository)
            await repository.set_panel_message_id(701)
            state = await repository.read()

            assert state.panel_message_id == 701
            assert state.next_bump_at == NOW + timedelta(hours=2)
            assert state.last_success_message_id == 500
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_duplicate_success_does_not_restart_cooldown(tmp_path):
    async def scenario():
        repository = BumpRepository(make_config(tmp_path), 100)
        try:
            await record(repository)
            before = await repository.read()

            accepted = await record(
                repository,
                occurred_at=NOW + timedelta(hours=3),
            )

            assert not accepted
            assert await repository.read() == before
            assert (await repository.read()).is_available(
                NOW + timedelta(hours=3)
            )
        finally:
            await repository.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("message_id", "offset"),
    [(499, 10800), (501, -3600)],
)
def test_older_success_does_not_replace_newer_state(tmp_path, message_id, offset):
    async def scenario():
        repository = BumpRepository(make_config(tmp_path), 100)
        try:
            await record(repository)
            before = await repository.read()

            accepted = await record(
                repository,
                message_id=message_id,
                occurred_at=NOW + timedelta(seconds=offset),
            )

            assert not accepted
            assert await repository.read() == before
        finally:
            await repository.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("seconds", [0, -1, True, 1.5])
def test_invalid_cooldown_is_rejected(tmp_path, seconds):
    async def scenario():
        repository = BumpRepository(make_config(tmp_path), 100)
        try:
            with pytest.raises(ValueError):
                await record(repository, cooldown_seconds=seconds)
            assert await repository.read() == BumpState()
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_naive_datetime_is_rejected(tmp_path):
    async def scenario():
        repository = BumpRepository(make_config(tmp_path), 100)
        try:
            with pytest.raises(ValueError):
                await record(
                    repository,
                    occurred_at=datetime(2026, 10, 11, 12, 0),
                )
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_corrupt_state_is_not_overwritten(tmp_path):
    config = make_config(tmp_path)
    raw = GuildJsonStore(config, 100, "bump_state")
    raw.update(lambda data: {"unexpected": True})
    before = raw.path.read_bytes()

    async def scenario():
        repository = BumpRepository(config, 100)
        try:
            with pytest.raises(StorageError):
                await record(repository)
            assert raw.path.read_bytes() == before
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_concurrent_duplicate_events_are_accepted_once(tmp_path):
    async def scenario():
        repository = BumpRepository(make_config(tmp_path), 100)
        try:
            results = await asyncio.gather(
                record(repository),
                record(repository),
            )
            assert results.count(True) == 1
            assert results.count(False) == 1
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_available_exactly_at_deadline():
    state = BumpState(
        last_success_message_id=500,
        last_bumped_at=NOW,
        next_bump_at=NOW + timedelta(hours=2),
    )

    assert not state.is_available(
        NOW + timedelta(hours=2) - timedelta(microseconds=1)
    )
    assert state.is_available(NOW + timedelta(hours=2))
