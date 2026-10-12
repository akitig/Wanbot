import asyncio
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from wanbot.config import parse_config
from wanbot.welcome.questionnaire import QuestionnaireError
from wanbot.welcome.questionnaire_repository import (
    QuestionnaireConflict,
    QuestionnaireDataError,
    QuestionnaireRepository,
)
from wanbot.welcome.repository import WelcomeSession


def make_config(tmp_path):
    return parse_config({
        "DISCORD_TOKEN": "test-token",
        "PROD_GUILD_ID": "101",
        "TEST_GUILD_ID": "100",
        "RUNTIME_DATA_DIR": str(tmp_path / "runtime"),
    })


def session():
    now = datetime.now(timezone.utc)
    return WelcomeSession(
        session_id="a" * 32,
        user_id=300,
        channel_id=500,
        state="active",
        created_at=now,
        updated_at=now,
    )


async def submit_name(repository, value="おおかみ"):
    return await repository.submit(
        session().session_id,
        actor_id=300,
        expected_revision=0,
        question="name",
        value=value,
    )


def test_answers_survive_reopening(tmp_path):
    async def run():
        config = make_config(tmp_path)
        repository = QuestionnaireRepository(config, 100)
        try:
            initial = await repository.ensure(session())
            assert initial.revision == 0
            saved = await submit_name(repository)
        finally:
            await repository.close()

        reopened = QuestionnaireRepository(config, 100)
        try:
            restored = await reopened.get(session().session_id)
            assert restored == saved
            assert restored.progress.name == "おおかみ"
            assert restored.progress.next_question == "gender"
            assert await reopened.ensure(session()) == saved
        finally:
            await reopened.close()

    asyncio.run(run())


def test_other_user_cannot_answer(tmp_path):
    async def run():
        repository = QuestionnaireRepository(make_config(tmp_path), 100)
        try:
            initial = await repository.ensure(session())

            with pytest.raises(QuestionnaireConflict):
                await repository.submit(
                    session().session_id,
                    actor_id=301,
                    expected_revision=0,
                    question="name",
                    value="別の人",
                )

            assert await repository.get(session().session_id) == initial
        finally:
            await repository.close()

    asyncio.run(run())


def test_concurrent_answers_only_save_once(tmp_path):
    async def run():
        config = make_config(tmp_path)
        first = QuestionnaireRepository(config, 100)
        second = QuestionnaireRepository(config, 100)
        try:
            await first.ensure(session())

            results = await asyncio.gather(
                submit_name(first, "回答A"),
                submit_name(second, "回答B"),
                return_exceptions=True,
            )

            assert sum(
                isinstance(result, QuestionnaireConflict)
                for result in results
            ) == 1

            saved = await first.get(session().session_id)
            assert saved.revision == 1
            assert saved.progress.name in ("回答A", "回答B")
        finally:
            await first.close()
            await second.close()

    asyncio.run(run())


def test_stale_edit_cannot_overwrite_newer_answer(tmp_path):
    async def run():
        repository = QuestionnaireRepository(make_config(tmp_path), 100)
        try:
            await repository.ensure(session())
            await submit_name(repository)

            updated = await repository.edit(
                session().session_id,
                actor_id=300,
                expected_revision=1,
                question="name",
                value="新しい名前",
            )

            with pytest.raises(QuestionnaireConflict):
                await repository.edit(
                    session().session_id,
                    actor_id=300,
                    expected_revision=1,
                    question="name",
                    value="古い画面の名前",
                )

            assert await repository.get(session().session_id) == updated
            assert updated.progress.next_question == "gender"
        finally:
            await repository.close()

    asyncio.run(run())


def test_invalid_answer_does_not_change_saved_data(tmp_path):
    async def run():
        repository = QuestionnaireRepository(make_config(tmp_path), 100)
        try:
            initial = await repository.ensure(session())

            with pytest.raises(QuestionnaireError):
                await submit_name(repository, "   ")

            assert await repository.get(session().session_id) == initial
        finally:
            await repository.close()

    asyncio.run(run())


def test_rejoin_has_separate_answers(tmp_path):
    async def run():
        repository = QuestionnaireRepository(make_config(tmp_path), 100)
        try:
            old = session()
            new = replace(old, session_id="b" * 32, channel_id=501)

            await repository.ensure(old)
            saved = await submit_name(repository)
            fresh = await repository.ensure(new)

            assert fresh.progress.name is None
            assert fresh.revision == 0
            assert await repository.get(old.session_id) == saved
        finally:
            await repository.close()

    asyncio.run(run())


def test_guilds_have_separate_answers(tmp_path):
    async def run():
        config = make_config(tmp_path)
        first = QuestionnaireRepository(config, 100)
        second = QuestionnaireRepository(config, 101)
        try:
            await first.ensure(session())
            await submit_name(first)

            assert await second.get(session().session_id) is None
        finally:
            await first.close()
            await second.close()

    asyncio.run(run())


def test_corrupt_data_is_not_overwritten(tmp_path):
    async def run():
        repository = QuestionnaireRepository(make_config(tmp_path), 100)
        try:
            corrupt = {"state_version": 999, "records": {}}
            await repository._store.update(lambda data: corrupt)

            with pytest.raises(QuestionnaireDataError):
                await repository.ensure(session())

            assert await repository._store.read() == corrupt
        finally:
            await repository.close()

    asyncio.run(run())


def test_non_active_welcome_cannot_start_questions(tmp_path):
    async def run():
        repository = QuestionnaireRepository(make_config(tmp_path), 100)
        try:
            closed = replace(
                session(),
                state="closed",
                closed_reason="member_left",
            )

            with pytest.raises(QuestionnaireConflict):
                await repository.ensure(closed)

            assert await repository.get(session().session_id) is None
        finally:
            await repository.close()

    asyncio.run(run())


def test_existing_record_cannot_change_owner(tmp_path):
    async def run():
        repository = QuestionnaireRepository(make_config(tmp_path), 100)
        try:
            initial = await repository.ensure(session())

            with pytest.raises(QuestionnaireConflict):
                await repository.ensure(
                    replace(session(), user_id=301)
                )

            assert await repository.get(session().session_id) == initial
        finally:
            await repository.close()

    asyncio.run(run())
