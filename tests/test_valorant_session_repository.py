import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from wanbot.config import AppConfig
from wanbot.storage.json_store import GuildJsonStore, StorageError
from wanbot.valorant.questions import load_questions
from wanbot.valorant.session import SessionError, SessionStatus
from wanbot.valorant.session_repository import SessionRepository


START = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def make_config(tmp_path):
    config = Mock(spec=AppConfig)
    config.runtime_data_dir = tmp_path
    config.guild_for.side_effect = (
        lambda guild_id: object() if guild_id in (100, 200) else None
    )
    return config


def make_repository(config, guild_id=100):
    return SessionRepository(config, guild_id, load_questions())


async def start(repository, now=START):
    return await repository.start(
        user_id=10,
        invoker_id=20,
        timeout_seconds=1800,
        now=now,
    )


async def answer(repository, session, number=1, actor_id=10):
    return await repository.answer(
        user_id=10,
        session_id=session.session_id,
        actor_id=actor_id,
        question_id=f"q{number:02d}",
        choice_id="a",
        now=START + timedelta(seconds=number),
    )


async def complete(repository):
    session = await start(repository)
    for number in range(1, 16):
        session = await answer(repository, session, number)
    return session


def test_progress_survives_reopening(tmp_path):
    config = make_config(tmp_path)

    async def scenario():
        first = make_repository(config)
        try:
            original = await start(first)
            await answer(first, original)
        finally:
            await first.close()

        second = make_repository(config)
        try:
            restored = await second.get(10, now=START + timedelta(minutes=1))
            assert restored.session_id == original.session_id
            assert restored.current_question.id == "q02"
            assert dict(restored.answers) == {"q01": "a"}
        finally:
            await second.close()

    asyncio.run(scenario())


def test_concurrent_starts_create_only_one_session(tmp_path):
    config = make_config(tmp_path)

    async def scenario():
        repository = make_repository(config)
        try:
            results = await asyncio.gather(
                start(repository),
                start(repository),
                return_exceptions=True,
            )
            assert sum(isinstance(item, SessionError) for item in results) == 1

            stored = await repository.get(10, now=START)
            successful = next(
                item for item in results if not isinstance(item, Exception)
            )
            assert stored.session_id == successful.session_id
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_same_user_can_start_in_different_guilds(tmp_path):
    config = make_config(tmp_path)

    async def scenario():
        prod = make_repository(config, 100)
        test = make_repository(config, 200)
        try:
            first = await start(prod)
            second = await start(test)
            assert first.session_id != second.session_id
            assert first.guild_id == 100
            assert second.guild_id == 200
        finally:
            await prod.close()
            await test.close()

    asyncio.run(scenario())


def test_concurrent_answers_are_accepted_only_once(tmp_path):
    config = make_config(tmp_path)

    async def scenario():
        repository = make_repository(config)
        try:
            session = await start(repository)
            results = await asyncio.gather(
                answer(repository, session),
                answer(repository, session),
                return_exceptions=True,
            )
            assert sum(isinstance(item, SessionError) for item in results) == 1

            stored = await repository.get(10, now=START + timedelta(minutes=1))
            assert dict(stored.answers) == {"q01": "a"}
            assert stored.current_question.id == "q02"
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_other_user_cannot_change_saved_answers(tmp_path):
    config = make_config(tmp_path)

    async def scenario():
        repository = make_repository(config)
        try:
            session = await start(repository)
            with pytest.raises(SessionError):
                await answer(repository, session, actor_id=99)

            stored = await repository.get(10, now=START)
            assert dict(stored.answers) == {}
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_expired_session_can_be_replaced_but_old_button_is_rejected(tmp_path):
    config = make_config(tmp_path)

    async def scenario():
        repository = make_repository(config)
        try:
            old = await start(repository)
            later = START + timedelta(hours=1)
            new = await start(repository, now=later)

            with pytest.raises(SessionError):
                await repository.answer(
                    user_id=10,
                    session_id=old.session_id,
                    actor_id=10,
                    question_id="q01",
                    choice_id="a",
                    now=later + timedelta(seconds=1),
                )

            stored = await repository.get(10, now=later)
            assert stored.session_id == new.session_id
            assert dict(stored.answers) == {}
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_completion_remains_until_acknowledged(tmp_path):
    config = make_config(tmp_path)

    async def scenario():
        repository = make_repository(config)
        try:
            completed = await complete(repository)

            with pytest.raises(SessionError):
                await start(repository, now=START + timedelta(hours=1))

            restored = await repository.get(
                10,
                now=START + timedelta(hours=1),
            )
            assert restored.status is SessionStatus.COMPLETED
            assert len(restored.answers) == 15

            await repository.acknowledge_completed(
                user_id=10,
                session_id=completed.session_id,
                now=START + timedelta(hours=1),
            )
            assert await repository.get(10, now=START) is None

            new = await start(repository, now=START + timedelta(hours=1))
            assert new.session_id != completed.session_id
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_active_session_cannot_be_acknowledged(tmp_path):
    config = make_config(tmp_path)

    async def scenario():
        repository = make_repository(config)
        try:
            session = await start(repository)

            with pytest.raises(SessionError):
                await repository.acknowledge_completed(
                    user_id=10,
                    session_id=session.session_id,
                    now=START,
                )

            assert await repository.get(10, now=START) is not None
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_wrong_session_id_cannot_delete_completed_session(tmp_path):
    config = make_config(tmp_path)

    async def scenario():
        repository = make_repository(config)
        try:
            completed = await complete(repository)

            with pytest.raises(SessionError):
                await repository.acknowledge_completed(
                    user_id=10,
                    session_id="0" * 32,
                    now=START + timedelta(minutes=1),
                )

            stored = await repository.get(10, now=START + timedelta(minutes=1))
            assert stored.session_id == completed.session_id
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_corrupt_progress_is_not_overwritten(tmp_path):
    config = make_config(tmp_path)
    raw = GuildJsonStore(config, 100, "valorant_sessions")
    raw.update(lambda data: {"unexpected": {}})
    before = raw.path.read_bytes()

    async def scenario():
        repository = make_repository(config)
        try:
            with pytest.raises(StorageError):
                await start(repository)
            assert raw.path.read_bytes() == before
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_missing_session_returns_none(tmp_path):
    config = make_config(tmp_path)

    async def scenario():
        repository = make_repository(config)
        try:
            assert await repository.get(10, now=START) is None
        finally:
            await repository.close()

    asyncio.run(scenario())
