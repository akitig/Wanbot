from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import discord
import pytest

from wanbot.leave_log.models import DepartureDataError, RoleSnapshot
from wanbot.leave_log.raw_event import capture_raw_departure


NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def member_payload():
    member = Mock(spec=discord.Member)
    member.id = 300
    member.name = "test_user"
    member.display_name = "テスト"
    member.guild = SimpleNamespace(id=100)
    member.joined_at = NOW - timedelta(days=1)
    member.roles = [
        SimpleNamespace(id=100, name="@everyone"),
        SimpleNamespace(id=400, name="構成員"),
    ]
    return SimpleNamespace(guild_id=100, user=member)


def test_raw_event_with_member_keeps_roles_and_join_time():
    payload = member_payload()
    result = capture_raw_departure(
        payload, event_id="a" * 32, observed_at=NOW
    )
    assert result.display_name == "テスト"
    assert result.roles == (RoleSnapshot(400, "構成員"),)
    assert result.joined_at == NOW - timedelta(days=1)


def test_raw_event_with_user_records_unknown_member_details():
    user = Mock(spec=discord.User)
    user.id = 300
    user.name = "test_user"
    payload = SimpleNamespace(guild_id=100, user=user)

    result = capture_raw_departure(
        payload, event_id="a" * 32, observed_at=NOW
    )
    assert result.user_id == 300
    assert result.username == "test_user"
    assert result.display_name is None
    assert result.roles is None
    assert result.joined_at is None


def test_member_with_only_everyone_has_known_empty_roles():
    payload = member_payload()
    payload.user.roles = [SimpleNamespace(id=100, name="@everyone")]
    result = capture_raw_departure(
        payload, event_id="a" * 32, observed_at=NOW
    )
    assert result.roles == ()


def test_other_guild_member_is_rejected():
    payload = member_payload()
    payload.user.guild.id = 999
    with pytest.raises(DepartureDataError):
        capture_raw_departure(
            payload, event_id="a" * 32, observed_at=NOW
        )


def test_separate_departures_of_same_user_keep_separate_ids():
    first_payload = member_payload()
    second_payload = member_payload()
    second_payload.user.joined_at = NOW + timedelta(minutes=1)

    first = capture_raw_departure(
        first_payload, event_id="a" * 32, observed_at=NOW
    )
    second = capture_raw_departure(
        second_payload,
        event_id="b" * 32,
        observed_at=NOW + timedelta(minutes=2),
    )
    assert first.user_id == second.user_id
    assert first.event_id != second.event_id
    assert first.joined_at != second.joined_at
