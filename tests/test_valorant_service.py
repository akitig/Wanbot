import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock

import pytest

from wanbot.config import AppConfig
from wanbot.storage.json_store import StorageError
from wanbot.valorant.questions import load_questions
from wanbot.valorant.repository import DiagnosisRepository
from wanbot.valorant.scoring import DiagnosisScorer
from wanbot.valorant.service import DiagnosisService
from wanbot.valorant.session import SessionError, SessionStatus
from wanbot.valorant.session_repository import SessionRepository


START = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def make_config(tmp_path):
    config = Mock(spec=AppConfig)
    config.runtime_data_dir = tmp_path
    config.guild_for.side_effect = (
        lambda guild_id: object() if guild_id == 100 else None
    )
    return config


def make_scorer():
    return DiagnosisScorer(load_questions())


def make_service(config):
    return DiagnosisService(config, 100, make_scorer())


async def start(service):
    return await service.start(
        user_id=10,
        invoker_id=20,
        timeout_seconds=1800,
        now=START,
    )


async def answer(service, session, number):
    return await service.answer(
        user_id=10,
        session_id=session.session_id,
        actor_id=10,
        question_id=f"q{number:02d}",
        choice_id="a",
        now=START + timedelta(seconds=number),
    )


async def prepare_last_question(service):
    progress = await start(service)
    for number in range(1, 15):
        progress = await answer(service, progress.session, number)
    return progress.session


async def read_result(config):
    repository = DiagnosisRepository(config, 100, make_scorer())
    try:
        return await repository.latest(10)
    finally:
        await repository.close()


async def read_session(config):
    repository = SessionRepository(config, 100, load_questions())
    try:
        return await repository.get(10, now=START + timedelta(minutes=1))
    finally:
        await repository.close()


def test_full_diagnosis_saves_result_and_removes_progress(tmp_path):
    config = make_config(tmp_path)

    async def scenario():
        service = make_service(config)
        try:
            session = await prepare_last_question(service)
            assert await read_result(config) is None

            completed = await answer(service, session, 15)

            assert completed.session.status is SessionStatus.COMPLETED
            assert completed.result is not None

            saved = await read_result(config)
            assert len(saved["answers"]) == 15
            assert saved["category"] == completed.result.category.value
            assert saved["completed_at"] == (
                START + timedelta(seconds=15)
            ).isoformat()
            assert await read_session(config) is None
        finally:
            await service.close()

    asyncio.run(scenario())


def test_result_save_failure_can_be_recovered_after_restart(tmp_path, monkeypatch):
    config = make_config(tmp_path)

    async def scenario():
        first = make_service(config)
        try:
            session = await prepare_last_question(first)
            monkeypatch.setattr(
                first._results,
                "save_completed",
                AsyncMock(side_effect=StorageError("保存失敗")),
            )

            with pytest.raises(StorageError):
                await answer(first, session, 15)

            pending = await read_session(config)
            assert pending.status is SessionStatus.COMPLETED
            assert len(pending.answers) == 15
            assert await read_result(config) is None
        finally:
            await first.close()

        second = make_service(config)
        try:
            recovered = await second.resume(
                10,
                now=START + timedelta(hours=1),
            )
            assert recovered.result is not None

            saved = await read_result(config)
            assert saved["completed_at"] == (
                START + timedelta(seconds=15)
            ).isoformat()
            assert await read_session(config) is None
        finally:
            await second.close()

    asyncio.run(scenario())


def test_cleanup_failure_can_be_retried_without_changing_result(
    tmp_path,
    monkeypatch,
):
    config = make_config(tmp_path)

    async def scenario():
        first = make_service(config)
        try:
            session = await prepare_last_question(first)
            monkeypatch.setattr(
                first._sessions,
                "acknowledge_completed",
                AsyncMock(side_effect=StorageError("削除失敗")),
            )

            with pytest.raises(StorageError):
                await answer(first, session, 15)

            saved_before = await read_result(config)
            assert saved_before is not None
            assert await read_session(config) is not None
        finally:
            await first.close()

        second = make_service(config)
        try:
            recovered = await second.resume(
                10,
                now=START + timedelta(minutes=5),
            )
            assert recovered.result is not None
            assert await read_result(config) == saved_before
            assert await read_session(config) is None
        finally:
            await second.close()

    asyncio.run(scenario())


def test_partial_diagnosis_resumes_without_saving_result(tmp_path):
    config = make_config(tmp_path)

    async def scenario():
        first = make_service(config)
        try:
            progress = await start(first)
            await answer(first, progress.session, 1)
        finally:
            await first.close()

        second = make_service(config)
        try:
            restored = await second.resume(
                10,
                now=START + timedelta(minutes=1),
            )
            assert restored.session.current_question.id == "q02"
            assert restored.result is None
            assert await read_result(config) is None
        finally:
            await second.close()

    asyncio.run(scenario())


def test_expired_diagnosis_does_not_create_result(tmp_path):
    config = make_config(tmp_path)

    async def scenario():
        service = make_service(config)
        try:
            await start(service)
            restored = await service.resume(
                10,
                now=START + timedelta(hours=1),
            )
            assert restored.session.status is SessionStatus.EXPIRED
            assert restored.result is None
            assert await read_result(config) is None
        finally:
            await service.close()

    asyncio.run(scenario())


def test_concurrent_last_answers_complete_only_once(tmp_path):
    config = make_config(tmp_path)

    async def scenario():
        service = make_service(config)
        try:
            session = await prepare_last_question(service)
            outcomes = await asyncio.gather(
                answer(service, session, 15),
                answer(service, session, 15),
                return_exceptions=True,
            )

            assert sum(
                isinstance(item, SessionError) for item in outcomes
            ) == 1
            assert await read_result(config) is not None
            assert await read_session(config) is None
        finally:
            await service.close()

    asyncio.run(scenario())


def test_missing_session_returns_none(tmp_path):
    config = make_config(tmp_path)

    async def scenario():
        service = make_service(config)
        try:
            assert await service.resume(10, now=START) is None
        finally:
            await service.close()

    asyncio.run(scenario())


def test_closed_service_rejects_new_operations(tmp_path):
    config = make_config(tmp_path)

    async def scenario():
        service = make_service(config)
        await service.close()
        await service.close()

        with pytest.raises(RuntimeError):
            await start(service)
        with pytest.raises(RuntimeError):
            await service.resume(10, now=START)

    asyncio.run(scenario())
