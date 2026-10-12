from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from wanbot.leave_log.capture import capture_departure
from wanbot.leave_log.models import DepartureDataError, RoleSnapshot


NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def arguments():
    return {
        "event_id": "a" * 32,
        "guild_id": 100,
        "user": SimpleNamespace(id=300, name="test_user"),
        "member": SimpleNamespace(
            id=300,
            guild=SimpleNamespace(id=100),
            display_name="テスト",
            joined_at=NOW - timedelta(days=1),
            roles=[
                SimpleNamespace(id=100, name="@everyone"),
                SimpleNamespace(id=400, name="構成員"),
                SimpleNamespace(id=401, name="Enjoy"),
            ],
        ),
        "observed_at": NOW,
    }


def test_captures_member_details_without_everyone():
    result = capture_departure(**arguments())
    assert result.user_id == 300
    assert result.username == "test_user"
    assert result.display_name == "テスト"
    assert result.roles == (
        RoleSnapshot(400, "構成員"),
        RoleSnapshot(401, "Enjoy"),
    )
    assert result.joined_at == NOW - timedelta(days=1)


def test_missing_member_keeps_available_user_information():
    args = arguments()
    args["member"] = None
    result = capture_departure(**args)
    assert result.user_id == 300
    assert result.username == "test_user"
    assert result.display_name is None
    assert result.roles is None
    assert result.joined_at is None


def test_everyone_only_is_known_empty_role_list():
    args = arguments()
    args["member"].roles = [SimpleNamespace(id=100, name="@everyone")]
    assert capture_departure(**args).roles == ()


def test_unavailable_roles_are_not_known_empty():
    args = arguments()
    args["member"].roles = None
    assert capture_departure(**args).roles is None


def test_other_member_is_rejected():
    args = arguments()
    args["member"].id = 999
    with pytest.raises(DepartureDataError):
        capture_departure(**args)


def test_other_guild_is_rejected():
    args = arguments()
    args["member"].guild.id = 999
    with pytest.raises(DepartureDataError):
        capture_departure(**args)


def test_snapshot_does_not_change_with_original_objects():
    args = arguments()
    result = capture_departure(**args)

    args["member"].display_name = "変更後"
    args["member"].roles[1].name = "変更後のロール"
    args["member"].roles.clear()

    assert result.display_name == "テスト"
    assert result.roles[0] == RoleSnapshot(400, "構成員")
    assert len(result.roles) == 2


def test_unknown_join_time_is_allowed():
    args = arguments()
    args["member"].joined_at = None
    assert capture_departure(**args).joined_at is None


def test_event_identity_and_observation_time_are_preserved():
    args = arguments()
    result = capture_departure(**args)
    assert result.event_id == args["event_id"]
    assert result.observed_at == NOW
