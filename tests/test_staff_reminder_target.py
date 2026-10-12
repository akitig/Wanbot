from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from wanbot.welcome.staff_reminder_target import (
    StaffReminderTargetError,
    select_staff_target,
)


NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)
SID = "a" * 32


def context():
    return SimpleNamespace(
        guild_id=100,
        session_id=SID,
        user_id=300,
        channel_id=500,
    )


def request():
    return SimpleNamespace(
        session_id=SID,
        user_id=300,
        channel_id=500,
        confirmed_at=NOW,
    )


def plan(status):
    return SimpleNamespace(
        welcome_session_id=SID,
        guild_id=100,
        user_id=300,
        channel_id=500,
        proposal_id="b" * 32,
        status=status,
    )


def kick(status="pending"):
    return {
        "guild_id": 100,
        "session_id": SID,
        "user_id": 300,
        "channel_id": 500,
        "request_id": "c" * 32,
        "status": status,
    }


def select(**changes):
    values = {
        "context": context(),
        "request": request(),
        "plan": None,
        "kick_reviews": [],
    }
    values.update(changes)
    return select_staff_target(**values)


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (None, "date_selection"),
        ("waiting_second", "second_manager"),
        ("declined", "date_selection"),
        ("confirmed", None),
    ],
)
def test_meeting_wait_state(state, expected):
    target = select(plan=None if state is None else plan(state))
    assert (target.kind if target else None) == expected


def test_no_preferences_means_no_reminder():
    assert select(request=None) is None


def test_resubmission_has_a_new_reminder_key():
    first = request()
    second = request()
    second.confirmed_at += timedelta(hours=1)
    assert select(request=first).key != select(request=second).key


def test_same_submission_keeps_reminder_key():
    assert select().key == select().key


def test_new_second_manager_proposal_has_a_new_key():
    first = plan("waiting_second")
    second = plan("waiting_second")
    second.proposal_id = "d" * 32
    assert select(plan=first).key != select(plan=second).key


@pytest.mark.parametrize("status", ["pending", "approved"])
def test_kick_review_takes_priority(status):
    target = select(kick_reviews=[kick(status)])
    assert target.kind == "kick_review"
    assert target.key == f"kick:{'c' * 32}"


@pytest.mark.parametrize("status", ["executing", "completed"])
def test_kick_execution_is_not_reminded(status):
    assert select(kick_reviews=[kick(status)]) is None


@pytest.mark.parametrize("status", ["cancelled", "obsolete"])
def test_inactive_kick_does_not_block_date_selection(status):
    assert select(kick_reviews=[kick(status)]).kind == "date_selection"


@pytest.mark.parametrize(
    "changes",
    [
        {"active": False},
        {"admission_pending": True},
        {"no_show_pending": True},
    ],
)
def test_non_staff_wait_is_not_reminded(changes):
    assert select(**changes) is None


def test_no_show_kick_review_can_be_reminded():
    target = select(no_show_pending=True, kick_reviews=[kick()])
    assert target.kind == "kick_review"


def test_other_guild_kick_is_rejected():
    record = kick()
    record["guild_id"] = 999
    with pytest.raises(StaffReminderTargetError):
        select(kick_reviews=[record])


def test_other_session_request_is_rejected():
    item = request()
    item.session_id = "e" * 32
    with pytest.raises(StaffReminderTargetError):
        select(request=item)


def test_other_channel_plan_is_rejected():
    item = plan("waiting_second")
    item.channel_id = 999
    with pytest.raises(StaffReminderTargetError):
        select(plan=item)


def test_multiple_pending_kicks_are_rejected():
    first = kick()
    second = kick()
    second["request_id"] = "d" * 32
    with pytest.raises(StaffReminderTargetError):
        select(kick_reviews=[first, second])


def test_unknown_plan_status_is_rejected():
    with pytest.raises(StaffReminderTargetError):
        select(plan=plan("unknown"))


def test_naive_submission_time_is_rejected():
    item = request()
    item.confirmed_at = NOW.replace(tzinfo=None)
    with pytest.raises(StaffReminderTargetError):
        select(request=item)
