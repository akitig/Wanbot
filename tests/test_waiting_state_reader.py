"""実際の質問形式と、本人待ち判定の接続。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wanbot.valorant.session import SessionStatus
from wanbot.welcome.questionnaire import Questionnaire
from wanbot.welcome.waiting_state_reader import (
    WaitingStateError,
    WaitingStateReader,
)


SID = "a" * 32


def complete_questionnaire(plays_valo=False):
    return Questionnaire(
        name="テスト",
        gender="no_answer",
        adult=True,
        main_game="ゲーム",
        comment="よろしく",
        hobby="読書",
        plays_valo=plays_valo,
    )


def setup(progress=None):
    context = SimpleNamespace(
        session_id=SID, guild_id=100, user_id=300, channel_id=500
    )
    controller = SimpleNamespace(
        _context=context,
        _session=AsyncMock(return_value=SimpleNamespace(state="active")),
        _admissions=SimpleNamespace(get=AsyncMock(return_value=None)),
        _kick_review=SimpleNamespace(
            _repository=SimpleNamespace(
                for_session=AsyncMock(return_value=[])
            )
        ),
        load_request=AsyncMock(return_value=None),
        _plan=AsyncMock(return_value=None),
        _repository=SimpleNamespace(
            archived_for_reschedule=AsyncMock(return_value=None)
        ),
        _meetings=SimpleNamespace(
            archived_for_reschedule=AsyncMock(return_value=None)
        ),
    )
    snapshot = SimpleNamespace(
        session_id=SID,
        user_id=300,
        revision=7,
        progress=progress if progress is not None else complete_questionnaire(),
    )
    questions = SimpleNamespace(get=AsyncMock(return_value=snapshot))
    diagnosis = SimpleNamespace(load_progress=AsyncMock(return_value=None))
    rules = SimpleNamespace(has_reacted=AsyncMock(return_value=True))
    no_shows = SimpleNamespace(all_records=AsyncMock(return_value=[]))
    reader = WaitingStateReader(
        controller=controller,
        questions=questions,
        diagnosis=diagnosis,
        rules=rules,
        no_shows=no_shows,
    )
    return reader, controller, questions, diagnosis, rules, no_shows


def test_completed_questions_wait_for_dates():
    async def scenario():
        reader, controller, questions, diagnosis, rules, no_shows = setup()
        result = await reader.read_locked()
        assert result.decision.reason == "interview_preferences"
        assert result.decision.counts_applicant_time
        diagnosis.load_progress.assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "confirmed,owner,reason",
    [
        (False, "applicant", "introduction"),
        (True, "staff", "age_review"),
    ],
)
def test_age_confirmation_mapping(confirmed, owner, reason):
    async def scenario():
        progress = Questionnaire(
            name="テスト",
            gender="no_answer",
            adult=False,
            underage_confirmed=confirmed,
        )
        reader, controller, questions, diagnosis, rules, no_shows = setup(
            progress
        )
        result = await reader.read_locked()
        assert result.decision.owner == owner
        assert result.decision.reason == reason

    asyncio.run(scenario())


def test_submitted_dates_are_staff_waiting_without_rules_lookup():
    async def scenario():
        reader, controller, questions, diagnosis, rules, no_shows = setup()
        controller.load_request.return_value = object()
        rules.has_reacted.side_effect = RuntimeError("unavailable")

        result = await reader.read_locked()

        assert result.decision.owner == "staff"
        rules.has_reacted.assert_not_awaited()

    asyncio.run(scenario())


def test_confirmed_meeting_does_not_count():
    async def scenario():
        reader, controller, questions, diagnosis, rules, no_shows = setup()
        controller.load_request.return_value = object()
        controller._plan.return_value = SimpleNamespace(status="confirmed")

        result = await reader.read_locked()

        assert result.decision.owner == "scheduled"
        assert not result.decision.counts_applicant_time

    asyncio.run(scenario())


def test_same_progress_has_same_key_and_saved_answer_changes_key():
    async def scenario():
        reader, controller, questions, diagnosis, rules, no_shows = setup()
        first = await reader.read_locked()
        repeated = await reader.read_locked()
        assert first.progress_key == repeated.progress_key

        questions.get.return_value.revision += 1
        updated = await reader.read_locked()
        assert updated.progress_key != first.progress_key
        assert len(updated.progress_key) == 64

    asyncio.run(scenario())


def test_diagnosis_expiry_does_not_change_progress_key():
    async def scenario():
        reader, controller, questions, diagnosis, rules, no_shows = setup(
            complete_questionnaire(True)
        )
        session = SimpleNamespace(
            guild_id=100,
            user_id=300,
            session_id="b" * 32,
            status=SessionStatus.ACTIVE,
            answers={"q1": "a"},
        )
        diagnosis.load_progress.return_value = SimpleNamespace(
            session=session, result=None
        )
        first = await reader.read_locked()
        session.status = SessionStatus.EXPIRED
        expired = await reader.read_locked()

        assert first.decision.reason == "diagnosis"
        assert expired.progress_key == first.progress_key

    asyncio.run(scenario())


def test_saved_diagnosis_answer_changes_progress_key():
    async def scenario():
        reader, controller, questions, diagnosis, rules, no_shows = setup(
            complete_questionnaire(True)
        )
        session = SimpleNamespace(
            guild_id=100,
            user_id=300,
            session_id="b" * 32,
            status=SessionStatus.ACTIVE,
            answers={},
        )
        diagnosis.load_progress.return_value = SimpleNamespace(
            session=session, result=None
        )
        first = await reader.read_locked()
        session.answers["q1"] = "a"
        second = await reader.read_locked()
        assert second.progress_key != first.progress_key

    asyncio.run(scenario())


def test_rules_lookup_failure_is_not_treated_as_missing_reaction():
    async def scenario():
        reader, controller, questions, diagnosis, rules, no_shows = setup()
        rules.has_reacted.side_effect = RuntimeError("unavailable")

        with pytest.raises(RuntimeError):
            await reader.read_locked()

    asyncio.run(scenario())


def test_other_users_questionnaire_is_rejected():
    async def scenario():
        reader, controller, questions, diagnosis, rules, no_shows = setup()
        questions.get.return_value.user_id = 999

        with pytest.raises(WaitingStateError):
            await reader.read_locked()

    asyncio.run(scenario())


def test_no_show_one_hour_wait_is_excluded():
    async def scenario():
        reader, controller, questions, diagnosis, rules, no_shows = setup()
        no_shows.all_records.return_value = [{
            "session_id": SID,
            "guild_id": 100,
            "user_id": 300,
            "channel_id": 500,
            "proposal_id": "c" * 32,
            "status": "waiting",
        }]

        result = await reader.read_locked()

        assert result.decision.reason == "no_show_one_hour"
        assert not result.decision.counts_applicant_time

    asyncio.run(scenario())
