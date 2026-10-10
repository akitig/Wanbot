from datetime import datetime, timedelta, timezone

import pytest

from wanbot.valorant.questions import load_questions
from wanbot.valorant.session import (
    DiagnosisSession,
    SessionError,
    SessionStatus,
)


START = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def make_session(timeout_seconds=1800):
    return DiagnosisSession(
        guild_id=100,
        user_id=10,
        invoker_id=20,
        question_set=load_questions(),
        started_at=START,
        timeout_seconds=timeout_seconds,
    )


def answer(session, question_id="q01", choice_id="a", actor_id=10, now=None):
    return session.submit(
        actor_id=actor_id,
        question_id=question_id,
        choice_id=choice_id,
        now=now if now is not None else START + timedelta(seconds=1),
    )


def test_session_starts_at_first_question():
    session = make_session()

    assert session.status is SessionStatus.ACTIVE
    assert session.current_question.id == "q01"
    assert dict(session.answers) == {}
    assert session.completed_at is None


def test_answer_advances_to_next_question():
    session = make_session()

    assert answer(session) is SessionStatus.ACTIVE
    assert session.current_question.id == "q02"
    assert dict(session.answers) == {"q01": "a"}


def test_other_user_cannot_answer():
    session = make_session()

    with pytest.raises(SessionError):
        answer(session, actor_id=99)

    assert dict(session.answers) == {}
    assert session.current_question.id == "q01"


def test_duplicate_answer_does_not_change_previous_answer():
    session = make_session()
    answer(session, choice_id="a")

    with pytest.raises(SessionError):
        answer(session, choice_id="b")

    assert dict(session.answers) == {"q01": "a"}
    assert session.current_question.id == "q02"


def test_future_question_cannot_be_answered():
    session = make_session()

    with pytest.raises(SessionError):
        answer(session, question_id="q02")

    assert dict(session.answers) == {}


def test_unknown_choice_is_rejected():
    session = make_session()

    with pytest.raises(SessionError):
        answer(session, choice_id="unknown")

    assert dict(session.answers) == {}


def test_answer_at_deadline_is_rejected():
    session = make_session()

    with pytest.raises(SessionError):
        answer(session, now=session.expires_at)

    assert session.status is SessionStatus.EXPIRED
    assert session.current_question is None
    assert dict(session.answers) == {}


def test_answer_just_before_deadline_is_accepted():
    session = make_session()

    answer(session, now=session.expires_at - timedelta(microseconds=1))

    assert session.status is SessionStatus.ACTIVE
    assert session.current_question.id == "q02"


def test_all_answers_complete_session_and_cannot_be_changed():
    session = make_session()
    completed_at = START + timedelta(minutes=1)

    for number in range(1, 16):
        answer(
            session,
            question_id=f"q{number:02d}",
            now=completed_at,
        )

    assert session.status is SessionStatus.COMPLETED
    assert session.completed_at == completed_at
    assert session.current_question is None
    assert len(session.answers) == 15

    with pytest.raises(SessionError):
        answer(session, question_id="q15", choice_id="b", now=completed_at)

    assert session.answers["q15"] == "a"
    assert not session.expire_if_due(session.expires_at)
    assert session.status is SessionStatus.COMPLETED


def test_expiration_keeps_existing_answers_and_happens_once():
    session = make_session()
    answer(session)

    assert session.expire_if_due(session.expires_at)
    assert not session.expire_if_due(session.expires_at)
    assert dict(session.answers) == {"q01": "a"}

    with pytest.raises(SessionError):
        answer(session, question_id="q02", now=session.expires_at)


def test_answer_snapshot_cannot_modify_session():
    session = make_session()
    snapshot = session.answers

    with pytest.raises(TypeError):
        snapshot["q01"] = "b"

    answer(session)

    assert dict(snapshot) == {}
    assert dict(session.answers) == {"q01": "a"}


def test_answer_before_start_is_rejected():
    session = make_session()

    with pytest.raises(SessionError):
        answer(session, now=START - timedelta(seconds=1))

    assert dict(session.answers) == {}


@pytest.mark.parametrize("timeout_seconds", [0, -1, True, 1.5])
def test_invalid_timeout_is_rejected(timeout_seconds):
    with pytest.raises(ValueError):
        make_session(timeout_seconds)


def test_each_session_has_a_different_id():
    first = make_session()
    second = make_session()

    assert first.session_id != second.session_id
