import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from wanbot.leave_log.audit_match import AuditEvidence, AuditMatch
from wanbot.leave_log.audit_reader import _actor_names
from wanbot.leave_log.models import DepartureSnapshot
from wanbot.leave_log.presentation import departure_embed
from wanbot.leave_log.result_repository import decode_result, encode_result


NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def snapshot():
    return DepartureSnapshot(
        event_id="a" * 32,
        guild_id=100,
        user_id=300,
        username="participant",
        display_name="参加者",
        roles=(),
        joined_at=None,
        observed_at=NOW,
    )


def evidence(**kwargs):
    return AuditEvidence(
        entry_id=500,
        guild_id=100,
        target_id=300,
        action="kick",
        created_at=NOW,
        actor_id=200,
        **kwargs,
    )


def result(**kwargs):
    return AuditMatch("kick", evidence(**kwargs), "matched")


def actor_field(match):
    embed = departure_embed(snapshot(), match)
    return next(field.value for field in embed.fields if field.name == "👮 実行者")


def test_actor_names_survive_storage():
    original = result(
        actor_username="wawolf_",
        actor_display_name="おおかみ",
    )
    saved = encode_result(snapshot(), original)
    restored_snapshot, restored = decode_result(saved)
    assert restored_snapshot == snapshot()
    assert restored == original


def test_legacy_result_remains_readable():
    saved = encode_result(snapshot(), result())
    assert "actor_username" not in saved["evidence"]
    assert "actor_display_name" not in saved["evidence"]
    restored_snapshot, restored = decode_result(saved)
    assert encode_result(restored_snapshot, restored) == saved
    assert restored.evidence.actor_username is None


def test_actor_display_uses_names_instead_of_id():
    text = actor_field(result(
        actor_username="wawolf",
        actor_display_name="おおかみ",
    ))
    assert text == "おおかみ（wawolf）"


def test_missing_actor_name_has_explicit_fallback():
    assert actor_field(result()) == "取得できませんでした（ID: 200）"


def test_actor_name_mentions_are_escaped():
    text = actor_field(result(
        actor_username="wawolf",
        actor_display_name="@everyone",
    ))
    assert "@everyone" not in text


def test_cached_member_nickname_is_preferred():
    member = SimpleNamespace(
        id=200,
        guild=SimpleNamespace(id=100),
        name="wawolf_",
        display_name="おおかみ",
    )
    guild = SimpleNamespace(
        id=100,
        get_member=lambda user_id: member,
        fetch_member=AsyncMock(),
    )
    entry = SimpleNamespace(user=SimpleNamespace(
        id=200, name="wawolf_", display_name="別の表示名",
    ))
    names = asyncio.run(_actor_names(guild, entry, 200))
    assert names == ("wawolf_", "おおかみ")
    guild.fetch_member.assert_not_awaited()


def test_uncached_member_is_fetched():
    member = SimpleNamespace(
        id=200,
        guild=SimpleNamespace(id=100),
        name="wawolf_",
        display_name="おおかみ",
    )
    guild = SimpleNamespace(
        id=100,
        get_member=lambda user_id: None,
        fetch_member=AsyncMock(return_value=member),
    )
    names = asyncio.run(_actor_names(
        guild, SimpleNamespace(user=None), 200,
    ))
    assert names == ("wawolf_", "おおかみ")
    guild.fetch_member.assert_awaited_once_with(200)


def test_member_fetch_failure_keeps_audit_user_name():
    response = SimpleNamespace(status=404, reason="Not Found")
    error = discord.NotFound(
        response, {"code": 10007, "message": "Unknown Member"},
    )
    guild = SimpleNamespace(
        id=100,
        get_member=lambda user_id: None,
        fetch_member=AsyncMock(side_effect=error),
    )
    entry = SimpleNamespace(user=SimpleNamespace(
        id=200, name="wawolf_", display_name="おおかみ",
    ))
    names = asyncio.run(_actor_names(guild, entry, 200))
    assert names == ("wawolf_", "おおかみ")
