from dataclasses import replace
from datetime import datetime, timezone

import pytest

from wanbot.leave_log.audit_match import AuditEvidence, AuditMatch
from wanbot.leave_log.models import DepartureSnapshot
from wanbot.leave_log.presentation import departure_embed
from wanbot.leave_log.result_repository import DepartureResultConflict


NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def snapshot():
    return DepartureSnapshot(
        event_id="a" * 32, guild_id=100, user_id=300,
        username="test_user", display_name=None, roles=None,
        joined_at=None, observed_at=NOW,
    )


def result(action="kick"):
    return AuditMatch(
        action,
        AuditEvidence(
            entry_id=1000, guild_id=100, target_id=300,
            action=action, created_at=NOW,
            actor_id=400, reason="テスト理由",
        ),
        "matched",
    )


def field(embed, name):
    return next(item.value for item in embed.fields if item.name == name)


@pytest.mark.parametrize(
    ("action", "label"),
    [("kick", "キック"), ("ban", "BAN")],
)
def test_matched_result_displays_action_actor_and_reason(action, label):
    embed = departure_embed(snapshot(), result(action))
    assert embed.description == f"退出の種類：{label}"
    assert field(embed, "実行者") == "取得できませんでした（ID: 400）"
    assert field(embed, "理由") == "テスト理由"
    assert field(embed, "監査ログID") == "1000"


def test_missing_actor_and_reason_are_explicit():
    matched = replace(
        result(),
        evidence=replace(result().evidence, actor_id=None, reason=None),
    )
    embed = departure_embed(snapshot(), matched)
    assert field(embed, "実行者") == "取得できませんでした。"
    assert field(embed, "理由") == "理由は未記入、または取得できませんでした。"


def test_long_reason_fits_field_limit():
    matched = replace(
        result(),
        evidence=replace(result().evidence, reason="*" * 3000),
    )
    embed = departure_embed(snapshot(), matched)
    assert len(field(embed, "理由")) <= 1000
    assert all(len(item.value) <= 1024 for item in embed.fields)
    assert len(embed) <= 6000


def test_no_audit_does_not_assert_voluntary_departure():
    embed = departure_embed(
        snapshot(), AuditMatch("unknown", None, "no_matching_audit")
    )
    assert embed.description == "退出の種類：判別できませんでした。"
    assert "断定はできません" in field(embed, "判定について")
    assert not any(item.name == "実行者" for item in embed.fields)


def test_failed_lookup_is_explained():
    embed = departure_embed(
        snapshot(),
        AuditMatch("unknown", None, "audit_unavailable_or_incomplete"),
    )
    assert "取得" in field(embed, "判定について")


def test_wrong_target_result_is_rejected():
    wrong = replace(
        result(),
        evidence=replace(result().evidence, target_id=999),
    )
    with pytest.raises(DepartureResultConflict):
        departure_embed(snapshot(), wrong)


def test_event_marker_is_preserved_with_result():
    embed = departure_embed(snapshot(), result())
    assert embed.footer.text == f"wanbot:leave-log:{'a' * 32}"
