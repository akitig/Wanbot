import asyncio
from datetime import datetime, timezone

import pytest

from wanbot.config import parse_config
from wanbot.valorant.questions import load_questions
from wanbot.valorant.scoring import DiagnosisScorer
from wanbot.valorant.service import DiagnosisService
from wanbot.valorant.session import SessionError, SessionStatus
from wanbot.welcome.valorant_service import WelcomeDiagnosisService


NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)


def make_config(tmp_path):
    return parse_config(
        {
            "DISCORD_TOKEN": "test-token",
            "TEST_GUILD_ID": "100",
            "RUNTIME_DATA_DIR": str(tmp_path / "runtime"),
        },
        base_dir=tmp_path,
    )


def make_service(config, scorer, session_id="a" * 32):
    return WelcomeDiagnosisService(
        config,
        100,
        scorer,
        welcome_session_id=session_id,
    )


async def start(service):
    return await service.start(
        user_id=300,
        invoker_id=300,
        timeout_seconds=3600,
        now=NOW,
    )


def test_welcome_and_dm_progress_are_separate(tmp_path):
    async def scenario():
        config = make_config(tmp_path)
        scorer = DiagnosisScorer(load_questions())
        welcome = make_service(config, scorer)
        dm = DiagnosisService(config, 100, scorer)
        try:
            welcome_progress = await start(welcome)
            assert await dm.resume(300, now=NOW) is None

            dm_progress = await start(dm)
            assert (
                dm_progress.session.session_id
                != welcome_progress.session.session_id
            )

            resumed = await welcome.resume(300, now=NOW)
            assert (
                resumed.session.session_id
                == welcome_progress.session.session_id
            )
        finally:
            await welcome.close()
            await dm.close()

    asyncio.run(scenario())


def test_different_welcome_sessions_are_separate(tmp_path):
    async def scenario():
        config = make_config(tmp_path)
        scorer = DiagnosisScorer(load_questions())
        first = make_service(config, scorer, "a" * 32)
        second = make_service(config, scorer, "b" * 32)
        try:
            original = await start(first)
            assert await second.resume(300, now=NOW) is None
            another = await start(second)
            assert (
                original.session.session_id
                != another.session.session_id
            )
        finally:
            await first.close()
            await second.close()

    asyncio.run(scenario())


def test_completion_survives_reopening_and_updates_shared_log(tmp_path):
    async def scenario():
        config = make_config(tmp_path)
        scorer = DiagnosisScorer(load_questions())
        service = make_service(config, scorer)

        try:
            progress = await start(service)
            diagnosis_id = progress.session.session_id

            while progress.session.status is SessionStatus.ACTIVE:
                question = progress.session.current_question
                progress = await service.answer(
                    user_id=300,
                    session_id=diagnosis_id,
                    actor_id=300,
                    question_id=question.id,
                    choice_id=question.choices[0].id,
                    now=NOW,
                )

            assert progress.result is not None
            saved_answers = dict(progress.session.answers)
            saved_category = progress.result.category
        finally:
            await service.close()

        reopened = make_service(config, scorer)
        shared = DiagnosisService(config, 100, scorer)
        try:
            resumed = await reopened.resume(300, now=NOW)
            assert resumed.session.session_id == diagnosis_id
            assert resumed.session.status is SessionStatus.COMPLETED
            assert dict(resumed.session.answers) == saved_answers
            assert resumed.result.category == saved_category

            record = await shared.latest_record(300)
            assert record["answers"] == saved_answers
            assert record["category"] == saved_category.value

            # 完了後に新しい診断で置き換えることはできない。
            with pytest.raises(SessionError):
                await start(reopened)
        finally:
            await reopened.close()
            await shared.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("session_id", ["", "../test", "x" * 32, None])
def test_invalid_welcome_session_id_is_rejected(tmp_path, session_id):
    config = make_config(tmp_path)
    scorer = DiagnosisScorer(load_questions())
    with pytest.raises(ValueError):
        make_service(config, scorer, session_id)
