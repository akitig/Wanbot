import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from wanbot.config import AppConfig
from wanbot.storage.json_store import GuildJsonStore, StorageError
from wanbot.valorant.questions import load_questions
from wanbot.valorant.repository import DiagnosisRepository
from wanbot.valorant.scoring import (
    AnswerError,
    ClassificationPolicy,
    DiagnosisScorer,
)


NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def make_config(tmp_path):
    config = Mock(spec=AppConfig)
    config.runtime_data_dir = tmp_path
    config.guild_for.side_effect = (
        lambda guild_id: object() if guild_id in (100, 200) else None
    )
    return config


def make_scorer(policy=None):
    return DiagnosisScorer(load_questions(), policy=policy)


def make_answers():
    return {f"q{number:02d}": "a" for number in range(1, 16)}


async def save(repository, user_id=10, completed_at=NOW):
    return await repository.save_completed(
        user_id=user_id,
        invoker_id=20,
        answers=make_answers(),
        completed_at=completed_at,
    )


def test_result_survives_reopening(tmp_path):
    config = make_config(tmp_path)

    async def scenario():
        first = DiagnosisRepository(config, 100, make_scorer())
        try:
            saved = await save(first)
        finally:
            await first.close()

        second = DiagnosisRepository(config, 100, make_scorer())
        try:
            restored = await second.latest(10)
            assert restored == saved
            assert restored["answers"] == make_answers()
            assert restored["invoker_id"] == 20
            assert restored["completed_at"] == NOW.isoformat()
            assert len(restored["axes"]) == 6
        finally:
            await second.close()

    asyncio.run(scenario())


def test_guilds_and_users_are_separate(tmp_path):
    config = make_config(tmp_path)

    async def scenario():
        prod = DiagnosisRepository(config, 100, make_scorer())
        test = DiagnosisRepository(config, 200, make_scorer())
        try:
            await save(prod)
            assert await prod.latest(10) is not None
            assert await prod.latest(11) is None
            assert await test.latest(10) is None
        finally:
            await prod.close()
            await test.close()

    asyncio.run(scenario())


def test_newest_completion_is_kept(tmp_path):
    config = make_config(tmp_path)

    async def scenario():
        repository = DiagnosisRepository(config, 100, make_scorer())
        try:
            await save(repository)
            newer = await save(repository, completed_at=NOW + timedelta(hours=1))
            await save(repository, completed_at=NOW - timedelta(hours=1))
            assert await repository.latest(10) == newer
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_concurrent_saves_keep_both_users(tmp_path):
    config = make_config(tmp_path)

    async def scenario():
        repository = DiagnosisRepository(config, 100, make_scorer())
        try:
            await asyncio.gather(
                save(repository, user_id=10),
                save(repository, user_id=11),
            )
            assert await repository.latest(10) is not None
            assert await repository.latest(11) is not None
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_incomplete_answers_are_not_saved(tmp_path):
    config = make_config(tmp_path)

    async def scenario():
        repository = DiagnosisRepository(config, 100, make_scorer())
        try:
            with pytest.raises(AnswerError):
                await repository.save_completed(
                    user_id=10,
                    invoker_id=20,
                    answers={"q01": "a"},
                )
            assert await repository.latest(10) is None
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_naive_datetime_is_rejected(tmp_path):
    config = make_config(tmp_path)

    async def scenario():
        repository = DiagnosisRepository(config, 100, make_scorer())
        try:
            with pytest.raises(ValueError):
                await save(
                    repository,
                    completed_at=datetime(2026, 10, 11, 12, 0),
                )
            assert await repository.latest(10) is None
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_saved_policy_is_preserved_after_setting_change(tmp_path):
    config = make_config(tmp_path)

    async def scenario():
        original = DiagnosisRepository(config, 100, make_scorer())
        try:
            saved = await save(original)
        finally:
            await original.close()

        changed = DiagnosisRepository(
            config,
            100,
            make_scorer(
                ClassificationPolicy(
                    gachi_minimum=0.95,
                    neutral_minimum=0.90,
                )
            ),
        )
        try:
            assert await changed.latest(10) == saved
        finally:
            await changed.close()

    asyncio.run(scenario())


def test_invalid_record_is_not_overwritten(tmp_path):
    config = make_config(tmp_path)

    async def scenario():
        repository = DiagnosisRepository(config, 100, make_scorer())
        try:
            await save(repository)
        finally:
            await repository.close()

        raw = GuildJsonStore(config, 100, "valorant_diagnoses")

        def damage(data):
            data["latest"]["10"]["weighted_score"] = -1
            return data

        raw.update(damage)
        before = raw.path.read_bytes()

        reopened = DiagnosisRepository(config, 100, make_scorer())
        try:
            with pytest.raises(StorageError):
                await reopened.latest(10)
            with pytest.raises(StorageError):
                await save(reopened)
            assert raw.path.read_bytes() == before
        finally:
            await reopened.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("user_id", [True, 0, -1, "10", 2**64])
def test_invalid_user_id_is_rejected(tmp_path, user_id):
    config = make_config(tmp_path)

    async def scenario():
        repository = DiagnosisRepository(config, 100, make_scorer())
        try:
            with pytest.raises(ValueError):
                await save(repository, user_id=user_id)
            assert await repository.latest(10) is None
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_unregistered_guild_is_rejected(tmp_path):
    with pytest.raises(StorageError):
        DiagnosisRepository(make_config(tmp_path), 300, make_scorer())
