"""本人期限を適用する状態と、除外する状態の判定。"""

from dataclasses import replace

import pytest

from wanbot.welcome.waiting_policy import WaitingFacts, decide_waiting


def facts(**changes):
    base = WaitingFacts(
        session_state="active",
        introduction="completed",
        rules_done=True,
        plays_valorant=False,
        diagnosis_done=False,
        interview_submitted=False,
        meeting="none",
        admission_pending=False,
        kick_pending=False,
        no_show="none",
    )
    return replace(base, **changes)


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"introduction": "not_started"}, "introduction"),
        ({"introduction": "answering"}, "introduction"),
        ({"introduction": "age_confirmation"}, "introduction"),
        ({"rules_done": False}, "rules"),
        (
            {"plays_valorant": True, "diagnosis_done": False},
            "diagnosis",
        ),
        ({}, "interview_preferences"),
        (
            {"plays_valorant": True, "diagnosis_done": True},
            "interview_preferences",
        ),
    ],
)
def test_applicant_waiting_counts(changes, reason):
    result = decide_waiting(facts(**changes))
    assert result.owner == "applicant"
    assert result.reason == reason
    assert result.counts_applicant_time


@pytest.mark.parametrize(
    "changes",
    [
        {"interview_submitted": True},
        {"meeting": "waiting_second"},
        {"meeting": "declined"},
        {"admission_pending": True},
        {"kick_pending": True},
        {"introduction": "underage"},
        {"no_show": "second_confirmation"},
        {"no_show": "kick_required"},
    ],
)
def test_staff_waiting_does_not_count(changes):
    result = decide_waiting(facts(**changes))
    assert result.owner == "staff"
    assert not result.counts_applicant_time


def test_confirmed_meeting_does_not_count_even_if_rule_reaction_removed():
    result = decide_waiting(
        facts(meeting="confirmed", rules_done=False)
    )
    assert result.owner == "scheduled"
    assert not result.counts_applicant_time


def test_submitted_dates_do_not_return_to_applicant_waiting():
    result = decide_waiting(
        facts(interview_submitted=True, rules_done=False)
    )
    assert result.owner == "staff"


@pytest.mark.parametrize(
    "changes",
    [
        {"session_state": "preparing"},
        {"session_state": "archiving"},
        {"session_state": "closed"},
        {"session_state": "unknown"},
        {"no_show": "waiting"},
        {"no_show": "rescheduling"},
    ],
)
def test_excluded_states_do_not_count(changes):
    result = decide_waiting(facts(**changes))
    assert result.owner == "excluded"
    assert not result.counts_applicant_time


@pytest.mark.parametrize(
    "changes",
    [
        {"admission_pending": None},
        {"kick_pending": None},
        {"no_show": "unknown"},
        {"meeting": "unknown"},
        {"interview_submitted": None},
        {"introduction": "unknown"},
        {"rules_done": None},
        {"plays_valorant": None},
        {"plays_valorant": True, "diagnosis_done": None},
    ],
)
def test_unavailable_state_does_not_count(changes):
    result = decide_waiting(facts(**changes))
    assert result.owner == "unknown"
    assert not result.counts_applicant_time


def test_non_valorant_player_does_not_need_diagnosis():
    result = decide_waiting(
        facts(plays_valorant=False, diagnosis_done=None)
    )
    assert result.reason == "interview_preferences"
    assert result.counts_applicant_time


@pytest.mark.parametrize(
    "changes",
    [
        {"meeting": "unexpected"},
        {"no_show": "unexpected"},
        {"rules_done": 1},
        {"admission_pending": "false"},
    ],
)
def test_invalid_state_is_rejected(changes):
    with pytest.raises(ValueError):
        decide_waiting(facts(**changes))
