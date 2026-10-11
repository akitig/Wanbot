from __future__ import annotations

from dataclasses import dataclass


TARGET_NAMES = frozenset({"gachi", "enjoy"})


class RecruitmentInputError(ValueError):
    """募集フォームの入力不備。"""


def _text(
    value: str,
    *,
    label: str,
    required: bool,
    limit: int,
) -> str:
    if not isinstance(value, str):
        raise RecruitmentInputError(f"{label}は文字で入力してください。")

    result = value.strip()

    if required and not result:
        raise RecruitmentInputError(f"{label}を入力してください。")

    if len(result) > limit:
        raise RecruitmentInputError(
            f"{label}は{limit}文字以内で入力してください。"
        )

    return result


@dataclass(frozen=True)
class RecruitmentDraft:
    targets: frozenset[str]
    modes: str
    player_count: str
    party: str
    comment: str


def make_draft(
    *,
    targets: frozenset[str],
    modes: str,
    player_count: str,
    party: str = "",
    comment: str = "",
) -> RecruitmentDraft:
    if (
        not isinstance(targets, frozenset)
        or not targets
        or not targets <= TARGET_NAMES
    ):
        raise RecruitmentInputError(
            "通知先はGachi・Enjoyのどちらか、または両方を選んでください。"
        )

    return RecruitmentDraft(
        targets=targets,
        modes=_text(
            modes,
            label="募集モード",
            required=True,
            limit=200,
        ),
        player_count=_text(
            player_count,
            label="人数",
            required=True,
            limit=100,
        ),
        party=_text(
            party,
            label="パーティコード・URL",
            required=False,
            limit=300,
        ),
        comment=_text(
            comment,
            label="ひとこと",
            required=False,
            limit=500,
        ),
    )
