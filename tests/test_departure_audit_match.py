from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from wanbot.leave_log.audit_match import AuditEvidence, match_audit
from wanbot.leave_log.models import DepartureSnapshot


NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def snapshot():
    return DepartureSnapshot(
        event_id="a" * 32,
        guild_id=100,
        user_id=300,
        username="test_user",
        display_name=None,
        roles=None,
        joined_at=NOW - timedelta(days=1),
        observed_at=NOW,
    )


def evidence(action="kick"):
    return AuditEvidence(
        entry_id=1000,
        guild_id=100,
        target_id=300,
        action=action,
        created_at=NOW,
        actor_id=400,
        reason="テスト理由",
    )


@pytest.mark.parametrize("action", ["kick", "ban"])
def test_single_matching_action_keeps_actor_and_reason(action):
    entry = evidence(action)
    result = match_audit(snapshot(), [entry], lookup_complete=True)
    assert result.kind == action
    assert result.evidence == entry
    assert result.evidence.actor_id == 400
    assert result.evidence.reason == "テスト理由"


def test_no_audit_does_not_prove_voluntary_departure():
    result = match_audit(snapshot(), [], lookup_complete=True)
    assert result.kind == "unknown"
    assert result.explanation == "no_matching_audit"


def test_incomplete_lookup_does_not_confirm_partial_candidate():
    result = match_audit(
        snapshot(), [evidence()], lookup_complete=False
    )
    assert result.kind == "unknown"
    assert result.evidence is None


@pytest.mark.parametrize(
    "changes",
    [
        {"guild_id": 999},
        {"target_id": 999},
        {"created_at": NOW - timedelta(seconds=31)},
        {"created_at": NOW + timedelta(seconds=31)},
    ],
)
def test_unrelated_or_old_entries_are_excluded(changes):
    entry = replace(evidence(), **changes)
    result = match_audit(snapshot(), [entry], lookup_complete=True)
    assert result.kind == "unknown"


def test_previous_membership_audit_is_excluded():
    current = replace(snapshot(), joined_at=NOW - timedelta(seconds=5))
    old = replace(evidence(), created_at=NOW - timedelta(seconds=10))
    result = match_audit(current, [old], lookup_complete=True)
    assert result.kind == "unknown"


def test_used_audit_cannot_identify_another_departure():
    result = match_audit(
        snapshot(),
        [evidence()],
        lookup_complete=True,
        used_entry_ids={1000},
    )
    assert result.kind == "unknown"


def test_kick_and_ban_candidates_are_ambiguous():
    entries = [
        evidence("kick"),
        replace(evidence("ban"), entry_id=1001),
    ]
    result = match_audit(snapshot(), entries, lookup_complete=True)
    assert result.kind == "unknown"
    assert result.explanation == "multiple_matching_audits"


def test_duplicate_copy_of_same_entry_is_not_ambiguous():
    entry = evidence()
    result = match_audit(snapshot(), [entry, entry], lookup_complete=True)
    assert result.kind == "kick"


def test_conflicting_copies_are_not_accepted():
    entry = evidence()
    changed = replace(entry, action="ban")
    result = match_audit(snapshot(), [entry, changed], lookup_complete=True)
    assert result.kind == "unknown"
    assert result.explanation == "conflicting_audit_data"


def test_missing_actor_and_reason_remain_unknown():
    entry = replace(evidence(), actor_id=None, reason=None)
    result = match_audit(snapshot(), [entry], lookup_complete=True)
    assert result.kind == "kick"
    assert result.evidence.actor_id is None
    assert result.evidence.reason is None
