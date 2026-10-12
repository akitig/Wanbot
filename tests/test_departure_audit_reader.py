import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import discord

from wanbot.leave_log.audit_reader import read_departure_audit
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
        joined_at=None,
        observed_at=NOW,
    )


def entry(action, *, target_id=300):
    return SimpleNamespace(
        id=1000 if action == discord.AuditLogAction.kick else 1001,
        guild=SimpleNamespace(id=100),
        action=action,
        target=SimpleNamespace(id=target_id),
        created_at=NOW,
        user_id=400,
        user=None,
        reason="テスト理由",
    )


def guild_with(kicks=(), bans=(), error=None):
    calls = []

    async def audit_logs(**kwargs):
        calls.append(kwargs)
        if error is not None:
            raise error
        items = kicks if kwargs["action"] == discord.AuditLogAction.kick else bans
        for item in items:
            yield item

    return SimpleNamespace(
        id=100, unavailable=False, audit_logs=audit_logs, calls=calls,
    )


def test_waits_before_requesting_audit_logs():
    async def run():
        guild = guild_with()
        result = await read_departure_audit(
            guild, snapshot(), now=NOW + timedelta(seconds=44)
        )
        assert result.status == "waiting"
        assert result.complete is False
        assert guild.calls == []

    asyncio.run(run())


def test_fetches_both_actions_and_preserves_evidence():
    async def run():
        guild = guild_with(
            kicks=[entry(discord.AuditLogAction.kick)],
            bans=[entry(discord.AuditLogAction.ban)],
        )
        result = await read_departure_audit(
            guild, snapshot(), now=NOW + timedelta(seconds=45)
        )
        assert result.complete is True
        assert [item.action for item in result.entries] == ["kick", "ban"]
        assert result.entries[0].actor_id == 400
        assert result.entries[0].reason == "テスト理由"
        assert len(guild.calls) == 2
        assert guild.calls[0]["after"] < NOW
        assert guild.calls[0]["before"] > NOW

    asyncio.run(run())


def test_other_users_are_not_returned():
    async def run():
        guild = guild_with(
            kicks=[entry(discord.AuditLogAction.kick, target_id=999)]
        )
        result = await read_departure_audit(
            guild, snapshot(), now=NOW + timedelta(minutes=1)
        )
        assert result.complete is True
        assert result.entries == ()

    asyncio.run(run())


def test_limit_reached_is_not_complete():
    async def run():
        guild = guild_with(
            kicks=[entry(discord.AuditLogAction.kick)] * 201
        )
        result = await read_departure_audit(
            guild, snapshot(), now=NOW + timedelta(minutes=1)
        )
        assert result.complete is False
        assert result.status == "limit_reached"
        assert result.entries == ()

    asyncio.run(run())


def test_permission_failure_is_not_empty_success():
    async def run():
        response = SimpleNamespace(status=403, reason="Forbidden")
        guild = guild_with(error=discord.Forbidden(response, "test"))
        result = await read_departure_audit(
            guild, snapshot(), now=NOW + timedelta(minutes=1)
        )
        assert result.status == "forbidden"
        assert result.complete is False

    asyncio.run(run())


def test_http_failure_is_not_empty_success():
    async def run():
        response = SimpleNamespace(status=500, reason="Server error")
        guild = guild_with(error=discord.HTTPException(response, "test"))
        result = await read_departure_audit(
            guild, snapshot(), now=NOW + timedelta(minutes=1)
        )
        assert result.status == "http_error"
        assert result.complete is False

    asyncio.run(run())


def test_missing_actor_and_reason_are_preserved():
    async def run():
        item = entry(discord.AuditLogAction.ban)
        item.user_id = None
        item.reason = None
        guild = guild_with(bans=[item])
        result = await read_departure_audit(
            guild, snapshot(), now=NOW + timedelta(minutes=1)
        )
        assert result.complete is True
        assert result.entries[0].actor_id is None
        assert result.entries[0].reason is None

    asyncio.run(run())


def test_unknown_target_makes_lookup_incomplete():
    async def run():
        item = entry(discord.AuditLogAction.kick)
        item.target = None
        guild = guild_with(kicks=[item])
        result = await read_departure_audit(
            guild, snapshot(), now=NOW + timedelta(minutes=1)
        )
        assert result.complete is False
        assert result.status == "invalid_audit_data"

    asyncio.run(run())


def test_unavailable_guild_does_not_fetch():
    async def run():
        guild = guild_with()
        guild.unavailable = True
        result = await read_departure_audit(
            guild, snapshot(), now=NOW + timedelta(minutes=1)
        )
        assert result.status == "guild_unavailable"
        assert guild.calls == []

    asyncio.run(run())
