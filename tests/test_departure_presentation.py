from dataclasses import replace
from datetime import datetime, timezone

from wanbot.leave_log.models import DepartureSnapshot, RoleSnapshot
from wanbot.leave_log.presentation import departure_embed


def snapshot():
    return DepartureSnapshot(
        event_id="a" * 32,
        guild_id=100,
        user_id=300,
        username="test_user",
        display_name="テスト",
        roles=(RoleSnapshot(400, "構成員"),),
        joined_at=None,
        observed_at=datetime(2026, 10, 12, tzinfo=timezone.utc),
    )


def field(embed, name):
    labels = {
        "ユーザー": "👤 ユーザー",
        "ユーザーID": "🆔 ID",
        "退出時のロール": "🎭 退出時ロール",
        "理由": "📝 理由",
        "実行者": "👮 実行者",
        "サーバー参加日時": "📥 サーバー参加日時",
        "退出検知日時": "📤 退出検知日時",
        "判定について": "ℹ️ 判定について",
    }
    return next(
        item.value for item in embed.fields if item.name == labels[name]
    )


def test_contains_member_identity_and_event_marker():
    embed = departure_embed(snapshot())
    assert field(embed, "ユーザーID") == "`300`"
    assert "<@300>" in field(embed, "ユーザー")
    assert "テスト（test\\_user）" in field(embed, "ユーザー")
    assert embed.footer.text == f"wanbot:leave-log:{'a' * 32}"


def test_unknown_roles_are_not_reported_as_empty():
    unknown = departure_embed(replace(snapshot(), roles=None))
    empty = departure_embed(replace(snapshot(), roles=()))
    assert field(unknown, "退出時のロール") == "取得できませんでした。"
    assert field(empty, "退出時のロール") == "記録対象のロールはありません。"


def test_role_names_and_ids_are_preserved_in_display():
    embed = departure_embed(snapshot())
    assert field(embed, "退出時のロール") == "<@&400>"


def test_many_roles_fit_without_changing_saved_information():
    roles = tuple(
        RoleSnapshot(100000000000000000 + index, "長いロール名" * 20)
        for index in range(100)
    )
    original = replace(snapshot(), roles=roles)
    embed = departure_embed(original)
    text = field(embed, "退出時のロール")
    assert len(text) <= 1024
    assert "保存データに記録済み" in text
    assert original.roles == roles
    assert len(original.roles) == 100


def test_long_names_fit_embed_limits():
    original = replace(
        snapshot(),
        username="*" * 2000,
        display_name="表示名" * 2000,
    )
    embed = departure_embed(original)
    assert len(field(embed, "ユーザー")) <= 500
    assert all(len(item.value) <= 1024 for item in embed.fields)
    assert len(embed) <= 6000


def test_unknown_join_time_is_explicit():
    embed = departure_embed(snapshot())
    assert field(embed, "サーバー参加日時") == "取得できませんでした。"


def test_join_time_is_formatted_as_discord_timestamp():
    original = snapshot()
    embed = departure_embed(replace(
        original, joined_at=original.observed_at
    ))
    expected = f"<t:{int(original.observed_at.timestamp())}:F>"
    assert field(embed, "サーバー参加日時") == expected


def test_unclassified_departure_is_not_called_voluntary():
    embed = departure_embed(snapshot())
    assert embed.title == "🚪 ユーザーが退出しました"
    assert field(embed, "判定について") == (
        "退出の種類は、まだ判別できていません。"
    )
