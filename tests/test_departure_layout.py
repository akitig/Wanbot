from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

from wanbot.leave_log.capture import capture_departure
from wanbot.leave_log.models import (
    DepartureSnapshot,
    decode_snapshot,
    encode_snapshot,
)
from wanbot.leave_log.presentation import departure_embed


NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)
AVATAR = "https://cdn.discordapp.com/embed/avatars/0.png"


def snapshot():
    return DepartureSnapshot(
        event_id="a" * 32,
        guild_id=100,
        user_id=300,
        username="example",
        display_name="テスト",
        roles=(),
        joined_at=NOW,
        observed_at=NOW,
    )


def test_old_snapshot_remains_readable():
    original = snapshot()
    record = encode_snapshot(original)
    assert "avatar_url" not in record
    assert decode_snapshot(record) == original


def test_avatar_is_preserved_through_storage():
    original = replace(snapshot(), avatar_url=AVATAR)
    assert decode_snapshot(encode_snapshot(original)) == original


def test_avatar_is_captured_from_departing_user():
    user = SimpleNamespace(
        id=300,
        name="example",
        display_avatar=SimpleNamespace(url=AVATAR),
    )
    captured = capture_departure(
        event_id="b" * 32,
        guild_id=100,
        user=user,
        member=None,
        observed_at=NOW,
    )
    assert captured.avatar_url == AVATAR


def test_thumbnail_and_vertical_field_order():
    embed = departure_embed(replace(snapshot(), avatar_url=AVATAR))
    assert embed.thumbnail.url == AVATAR
    assert [field.name for field in embed.fields[:7]] == [
        "👤 ユーザー",
        "🆔 ID",
        "🎭 退出時ロール",
        "📝 理由",
        "👮 実行者",
        "📥 サーバー参加日時",
        "📤 退出検知日時",
    ]
    assert all(field.inline is False for field in embed.fields)
    expected_time = f"<t:{int(NOW.timestamp())}:F>"
    assert embed.fields[5].value == expected_time
    assert embed.fields[6].value == expected_time


def test_missing_avatar_does_not_prevent_display():
    embed = departure_embed(snapshot())
    assert embed.thumbnail.url is None
    assert "<@300>" in embed.fields[0].value
