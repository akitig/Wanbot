import pytest

from wanbot.recruitment.models import make_draft
from wanbot.recruitment.presentation import build_post


@pytest.mark.parametrize(
    ("targets", "expected"),
    [
        (frozenset({"gachi"}), [200]),
        (frozenset({"enjoy"}), [300]),
        (frozenset({"gachi", "enjoy"}), [200, 300]),
    ],
)
def test_only_selected_roles_are_allowed(targets, expected):
    draft = make_draft(
        targets=targets,
        modes="コンペ",
        player_count="あと2人",
    )

    post = build_post(
        draft,
        author_id=100,
        gachi_role_id=200,
        enjoy_role_id=300,
    )

    assert post.content == " ".join(
        f"<@&{role_id}>" for role_id in expected
    )
    assert [role.id for role in post.allowed_mentions.roles] == expected
    assert post.allowed_mentions.everyone is False
    assert post.allowed_mentions.users is False
    assert post.allowed_mentions.replied_user is False


def test_user_text_does_not_enter_notification_content():
    draft = make_draft(
        targets=frozenset({"enjoy"}),
        modes="アンレート",
        player_count="あと2人",
        comment="@everyone <@&999> <@888>",
    )

    post = build_post(
        draft,
        author_id=100,
        gachi_role_id=200,
        enjoy_role_id=300,
    )

    assert post.content == "<@&300>"
    assert [role.id for role in post.allowed_mentions.roles] == [300]
    assert post.allowed_mentions.everyone is False
    assert post.allowed_mentions.users is False


def test_optional_fields_are_omitted_when_empty():
    draft = make_draft(
        targets=frozenset({"enjoy"}),
        modes="アンレート",
        player_count="あと2人",
    )

    post = build_post(
        draft,
        author_id=100,
        gachi_role_id=200,
        enjoy_role_id=300,
    )

    names = [field.name for field in post.embed.fields]
    assert "パーティコード・URL" not in names
    assert "ひとこと" not in names


def test_optional_fields_are_shown_when_provided():
    draft = make_draft(
        targets=frozenset({"enjoy"}),
        modes="アンレート",
        player_count="あと2人",
        party="ABC123",
        comment="気軽にどうぞ",
    )

    post = build_post(
        draft,
        author_id=100,
        gachi_role_id=200,
        enjoy_role_id=300,
    )

    fields = {
        field.name: field.value
        for field in post.embed.fields
    }
    assert fields["パーティコード・URL"] == "ABC123"
    assert fields["ひとこと"] == "気軽にどうぞ"


def test_identical_role_ids_are_rejected():
    draft = make_draft(
        targets=frozenset({"gachi"}),
        modes="コンペ",
        player_count="あと2人",
    )

    with pytest.raises(ValueError):
        build_post(
            draft,
            author_id=100,
            gachi_role_id=200,
            enjoy_role_id=200,
        )
