from datetime import datetime, timedelta, timezone

import pytest

from wanbot.storage.json_store import StorageError
from wanbot.valorant.questions import load_questions
from wanbot.valorant.session import DiagnosisSession, SessionStatus
from wanbot.valorant.session_data import session_from_data, session_to_data


START = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def make_session():
    return DiagnosisSession(
        guild_id=100,
        user_id=10,
        invoker_id=20,
        question_set=load_questions(),
        started_at=START,
        timeout_seconds=1800,
    )


def answer(session, number):
    session.submit(
        actor_id=10,
        question_id=f"q{number:02d}",
        choice_id="a",
        now=START + timedelta(seconds=number),
    )


def restore(data, now=None, guild_id=100):
    return session_from_data(
        data,
        question_set=load_questions(),
        expected_guild_id=guild_id,
        now=now if now is not None else START + timedelta(minutes=5),
    )


def test_partial_session_resumes_at_next_question():
    session = make_session()
    answer(session, 1)
    answer(session, 2)

    restored = restore(session_to_data(session))

    assert restored.session_id == session.session_id
    assert restored.guild_id == 100
    assert restored.user_id == 10
    assert restored.invoker_id == 20
    assert restored.status is SessionStatus.ACTIVE
    assert restored.current_question.id == "q03"
    assert dict(restored.answers) == {"q01": "a", "q02": "a"}
    assert restored.expires_at == session.expires_at

    answer(restored, 3)
    assert restored.current_question.id == "q04"


def test_empty_session_is_restored():
    restored = restore(session_to_data(make_session()))

    assert restored.current_question.id == "q01"
    assert dict(restored.answers) == {}


def test_restart_does_not_extend_deadline():
    session = make_session()
    answer(session, 1)

    restored = restore(
        session_to_data(session),
        now=session.expires_at,
    )

    assert restored.status is SessionStatus.EXPIRED
    assert restored.expires_at == session.expires_at
    assert restored.current_question is None
    assert dict(restored.answers) == {"q01": "a"}


def test_completed_session_stays_completed_after_deadline():
    session = make_session()
    for number in range(1, 16):
        answer(session, number)

    restored = restore(
        session_to_data(session),
        now=session.expires_at + timedelta(days=1),
    )

    assert restored.status is SessionStatus.COMPLETED
    assert restored.completed_at == session.completed_at
    assert len(restored.answers) == 15


def test_expired_session_stays_expired():
    session = make_session()
    answer(session, 1)
    session.expire_if_due(session.expires_at)

    restored = restore(
        session_to_data(session),
        now=session.expires_at + timedelta(minutes=1),
    )

    assert restored.status is SessionStatus.EXPIRED
    assert dict(restored.answers) == {"q01": "a"}


def test_other_guild_is_rejected():
    with pytest.raises(StorageError):
        restore(session_to_data(make_session()), guild_id=200)


def test_gap_in_answer_sequence_is_rejected():
    data = session_to_data(make_session())
    data["answers"] = {"q02": "a"}

    with pytest.raises(StorageError):
        restore(data)


def test_unknown_choice_is_rejected():
    data = session_to_data(make_session())
    data["answers"] = {"q01": "unknown"}

    with pytest.raises(StorageError):
        restore(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 99),
        ("schema_version", True),
        ("diagnosis_version", "unknown"),
        ("session_id", "invalid"),
        ("status", "unknown"),
        ("completed_at", START.isoformat()),
        ("extra", "unexpected"),
    ],
)
def test_invalid_metadata_is_rejected(field, value):
    data = session_to_data(make_session())
    data[field] = value

    with pytest.raises(StorageError):
        restore(data)


def test_completed_status_requires_all_answers():
    data = session_to_data(make_session())
    data["status"] = "completed"
    data["completed_at"] = (START + timedelta(minutes=1)).isoformat()

    with pytest.raises(StorageError):
        restore(data)


def test_all_answers_cannot_remain_active():
    session = make_session()
    for number in range(1, 16):
        answer(session, number)

    data = session_to_data(session)
    data["status"] = "active"
    data["completed_at"] = None

    with pytest.raises(StorageError):
        restore(data)


def test_exported_answers_do_not_modify_session():
    session = make_session()
    answer(session, 1)

    data = session_to_data(session)
    data["answers"]["q01"] = "b"

    assert session.answers["q01"] == "a"
