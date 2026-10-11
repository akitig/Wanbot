import pytest

from wanbot.recruitment.models import (
    RecruitmentInputError,
    make_draft,
)


def test_required_fields_and_optional_empty_fields():
    draft = make_draft(
        targets=frozenset({"gachi"}),
        modes="コンペ",
        player_count="あと2人",
    )

    assert draft.targets == frozenset({"gachi"})
    assert draft.modes == "コンペ"
    assert draft.player_count == "あと2人"
    assert draft.party == ""
    assert draft.comment == ""


def test_multiple_targets_and_modes_are_allowed():
    draft = make_draft(
        targets=frozenset({"gachi", "enjoy"}),
        modes="アンレート、スイフト",
        player_count="何人でも",
        party="ABC123",
        comment="気軽にどうぞ",
    )

    assert draft.targets == frozenset({"gachi", "enjoy"})
    assert draft.modes == "アンレート、スイフト"


def test_surrounding_whitespace_is_removed():
    draft = make_draft(
        targets=frozenset({"enjoy"}),
        modes="  アンレート  ",
        player_count="  あと1人  ",
        party="   ",
        comment="  よろしくお願いします  ",
    )

    assert draft.modes == "アンレート"
    assert draft.player_count == "あと1人"
    assert draft.party == ""
    assert draft.comment == "よろしくお願いします"


@pytest.mark.parametrize(
    "targets",
    [
        frozenset(),
        frozenset({"unknown"}),
        frozenset({"gachi", "unknown"}),
    ],
)
def test_invalid_targets_are_rejected(targets):
    with pytest.raises(RecruitmentInputError):
        make_draft(
            targets=targets,
            modes="コンペ",
            player_count="あと2人",
        )


@pytest.mark.parametrize("field", ["modes", "player_count"])
def test_required_fields_cannot_be_blank(field):
    values = {
        "targets": frozenset({"enjoy"}),
        "modes": "アンレート",
        "player_count": "あと2人",
    }
    values[field] = "   "

    with pytest.raises(RecruitmentInputError):
        make_draft(**values)


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("modes", 200),
        ("player_count", 100),
        ("party", 300),
        ("comment", 500),
    ],
)
def test_field_length_limit(field, limit):
    values = {
        "targets": frozenset({"enjoy"}),
        "modes": "アンレート",
        "player_count": "あと2人",
    }

    values[field] = "あ" * limit
    draft = make_draft(**values)
    assert len(getattr(draft, field)) == limit

    values[field] = "あ" * (limit + 1)
    with pytest.raises(RecruitmentInputError):
        make_draft(**values)
