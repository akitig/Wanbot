from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from wanbot.config import ID_FIELDS, parse_config
from wanbot.permissions import AccessDenied
from wanbot.welcome.meeting_plan import (
    MeetingPlanConflict,
    propose_meeting,
    respond_to_meeting,
)


NOW = datetime(2026, 10, 11, 7, 0, tzinfo=timezone.utc)


def make_case(tmp_path):
    values = {
        "DISCORD_TOKEN": "test-token",
        "TEST_GUILD_ID": "100",
        "TEST_ENABLED_FEATURES": "welcome,valorant_diagnosis",
        "TEST_ADMIN_ROLE_IDS": "400,401",
        "TEST_RULES_EMOJI": "👍",
        "RUNTIME_DATA_DIR": str(tmp_path / "runtime"),
    }
    values.update({
        f"TEST_{name}": str(1000 + index)
        for index, name in enumerate(ID_FIELDS)
    })
    config = parse_config(values, base_dir=tmp_path)
    request = SimpleNamespace(
        session_id="a" * 32,
        user_id=300,
        channel_id=500,
        preferences=tuple(
            NOW + timedelta(days=number)
            for number in (1, 2, 3)
        ),
    )
    return {
        "config": config,
        "guild_id": 100,
        "request": request,
        "actor_id": 200,
        "actor_role_ids": [400],
        "preference": 2,
        "second_manager_id": 201,
        "second_manager_role_ids": [401],
        "now": NOW,
    }


def respond(plan, options, **overrides):
    arguments = {
        "config": options["config"],
        "proposal_id": plan.proposal_id,
        "actor_id": 201,
        "actor_role_ids": [401],
        "accepted": True,
        "now": NOW + timedelta(minutes=1),
    }
    arguments.update(overrides)
    return respond_to_meeting(plan, **arguments)


def test_no_second_manager_confirms_immediately(tmp_path):
    options = make_case(tmp_path)
    options["second_manager_id"] = None
    options["second_manager_role_ids"] = []
    plan = propose_meeting(**options)
    assert plan.status == "confirmed"
    assert plan.first_manager_id == 200
    assert plan.meeting_at == options["request"].preferences[1]
    assert plan.decided_at == NOW


def test_second_manager_must_accept_before_confirmation(tmp_path):
    options = make_case(tmp_path)
    plan = propose_meeting(**options)
    assert plan.status == "waiting_second"
    assert plan.decided_at is None
    confirmed = respond(plan, options)
    assert confirmed.status == "confirmed"


@pytest.mark.parametrize("field", ["actor_role_ids", "second_manager_role_ids"])
def test_non_manager_cannot_be_assigned(tmp_path, field):
    options = make_case(tmp_path)
    options[field] = [999]
    with pytest.raises(AccessDenied):
        propose_meeting(**options)


@pytest.mark.parametrize("second_id", [200, 300])
def test_second_manager_cannot_be_first_manager_or_applicant(tmp_path, second_id):
    options = make_case(tmp_path)
    options["second_manager_id"] = second_id
    with pytest.raises(MeetingPlanConflict):
        propose_meeting(**options)


def test_another_manager_cannot_accept(tmp_path):
    options = make_case(tmp_path)
    plan = propose_meeting(**options)
    with pytest.raises(AccessDenied):
        respond(plan, options, actor_id=202)


def test_removed_manager_role_prevents_acceptance(tmp_path):
    options = make_case(tmp_path)
    plan = propose_meeting(**options)
    with pytest.raises(AccessDenied):
        respond(plan, options, actor_role_ids=[])


def test_old_button_is_rejected(tmp_path):
    options = make_case(tmp_path)
    plan = propose_meeting(**options)
    with pytest.raises(MeetingPlanConflict):
        respond(plan, options, proposal_id="b" * 32)


def test_pending_plan_cannot_be_overwritten(tmp_path):
    options = make_case(tmp_path)
    plan = propose_meeting(**options)
    with pytest.raises(MeetingPlanConflict):
        propose_meeting(**options, previous=plan)


def test_decline_allows_new_selection(tmp_path):
    options = make_case(tmp_path)
    plan = propose_meeting(**options)
    declined = respond(plan, options, accepted=False)
    assert declined.status == "declined"

    replacement = propose_meeting(**options, previous=declined)
    assert replacement.proposal_id != plan.proposal_id
    assert replacement.status == "waiting_second"


def test_answered_confirmation_cannot_be_used_again(tmp_path):
    options = make_case(tmp_path)
    plan = respond(propose_meeting(**options), options)
    with pytest.raises(MeetingPlanConflict):
        respond(plan, options)


def test_past_meeting_cannot_be_accepted(tmp_path):
    options = make_case(tmp_path)
    plan = propose_meeting(**options)
    with pytest.raises(MeetingPlanConflict):
        respond(plan, options, now=plan.meeting_at)
