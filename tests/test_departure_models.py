from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from wanbot.leave_log.models import (
    DepartureDataError,
    DepartureSnapshot,
    RoleSnapshot,
    decode_snapshot,
    encode_snapshot,
)


NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def snapshot():
    return DepartureSnapshot(
        event_id="a" * 32,
        guild_id=100,
        user_id=300,
        username="test_user",
        display_name="テスト",
        roles=(RoleSnapshot(400, "構成員"), RoleSnapshot(401, "Enjoy")),
        joined_at=NOW - timedelta(days=1),
        observed_at=NOW,
    )


def test_snapshot_round_trip():
    original = snapshot()
    assert decode_snapshot(encode_snapshot(original)) == original


def test_unknown_roles_and_empty_roles_are_different():
    unknown = replace(snapshot(), roles=None)
    empty = replace(snapshot(), roles=())
    assert encode_snapshot(unknown)["roles"] is None
    assert encode_snapshot(empty)["roles"] == []
    assert decode_snapshot(encode_snapshot(unknown)).roles is None
    assert decode_snapshot(encode_snapshot(empty)).roles == ()


def test_missing_member_information_can_be_recorded():
    original = replace(
        snapshot(), display_name=None, joined_at=None, roles=None
    )
    assert decode_snapshot(encode_snapshot(original)) == original


def test_duplicate_roles_are_rejected():
    with pytest.raises(DepartureDataError):
        replace(
            snapshot(),
            roles=(RoleSnapshot(400, "A"), RoleSnapshot(400, "B")),
        )


@pytest.mark.parametrize("value", [True, 0, -1, 2**64, "100"])
def test_invalid_guild_id_is_rejected(value):
    with pytest.raises(DepartureDataError):
        replace(snapshot(), guild_id=value)


def test_naive_datetime_is_rejected():
    with pytest.raises(DepartureDataError):
        replace(snapshot(), observed_at=NOW.replace(tzinfo=None))


def test_future_join_time_is_rejected():
    with pytest.raises(DepartureDataError):
        replace(snapshot(), joined_at=NOW + timedelta(seconds=1))


def test_extra_saved_fields_are_rejected():
    record = encode_snapshot(snapshot())
    record["unexpected"] = True
    with pytest.raises(DepartureDataError):
        decode_snapshot(record)


def test_invalid_saved_role_is_rejected():
    record = encode_snapshot(snapshot())
    record["roles"] = [{"role_id": 400}]
    with pytest.raises(DepartureDataError):
        decode_snapshot(record)


def test_timestamps_are_saved_in_utc():
    jst = timezone(timedelta(hours=9))
    original = replace(snapshot(), observed_at=NOW.astimezone(jst))
    assert encode_snapshot(original)["observed_at"] == NOW.isoformat()
