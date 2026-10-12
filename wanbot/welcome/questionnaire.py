"""参加者への質問の順番と、回答の入力チェック。"""

from dataclasses import dataclass, replace
import unicodedata


class QuestionnaireError(ValueError):
    """回答が不正、または現在の質問と一致しない。"""


TEXT_LIMITS = {
    "name": 100,
    "main_game": 200,
    "comment": 500,
    "hobby": 300,
}

GENDERS = frozenset({"female", "male", "no_answer"})


@dataclass(frozen=True)
class Questionnaire:
    name: str | None = None
    gender: str | None = None
    adult: bool | None = None
    underage_confirmed: bool = False
    main_game: str | None = None
    comment: str | None = None
    hobby: str | None = None
    plays_valo: bool | None = None

    @property
    def next_question(self) -> str | None:
        if self.name is None:
            return "name"
        if self.gender is None:
            return "gender"
        if self.adult is None:
            return "adult"
        if self.adult is False:
            if not self.underage_confirmed:
                return "age_confirmation"
            return None
        if self.main_game is None:
            return "main_game"
        if self.comment is None:
            return "comment"
        if self.hobby is None:
            return "hobby"
        if self.plays_valo is None:
            return "plays_valo"
        return None

    @property
    def status(self) -> str:
        if self.adult is False and self.underage_confirmed:
            return "age_review_required"
        if self.next_question is None:
            return "completed"
        return "answering"

    @property
    def needs_valorant_diagnosis(self) -> bool:
        return self.status == "completed" and self.plays_valo is True


def _text_answer(question: str, value: str) -> str:
    if not isinstance(value, str):
        raise QuestionnaireError("Text answer is required")

    value = value.strip()
    if not value or len(value) > TEXT_LIMITS[question]:
        raise QuestionnaireError("Invalid answer length")

    # 改行・タブ以外の制御文字は受け付けない。
    if any(
        unicodedata.category(char) == "Cc" and char not in "\n\t"
        for char in value
    ):
        raise QuestionnaireError("Invalid control character")

    # 不可視文字や空白だけの回答は受け付けない。
    if not any(
        not char.isspace()
        and not unicodedata.category(char).startswith("C")
        for char in value
    ):
        raise QuestionnaireError("Visible text is required")

    return value


def _yes_no(value: str) -> bool:
    if value == "yes":
        return True
    if value == "no":
        return False
    raise QuestionnaireError("Invalid selection")


def answer(
    progress: Questionnaire,
    question: str,
    value: str,
) -> Questionnaire:
    """現在の質問に回答し、新しい進行状態を返す。"""
    if (
        progress.status != "answering"
        or question != progress.next_question
    ):
        raise QuestionnaireError("Question is not current")

    if question in TEXT_LIMITS:
        return replace(
            progress,
            **{question: _text_answer(question, value)},
        )

    if question == "gender":
        if value not in GENDERS:
            raise QuestionnaireError("Invalid gender selection")
        return replace(progress, gender=value)

    if question == "adult":
        return replace(progress, adult=_yes_no(value))

    if question == "age_confirmation":
        if value == "under_25":
            return replace(progress, underage_confirmed=True)
        if value == "adult_correction":
            return replace(
                progress,
                adult=True,
                underage_confirmed=False,
            )
        raise QuestionnaireError("Invalid age confirmation")

    if question == "plays_valo":
        return replace(progress, plays_valo=_yes_no(value))

    raise QuestionnaireError("Unknown question")


def edit_text_answer(
    progress: Questionnaire,
    question: str,
    value: str,
) -> Questionnaire:
    """回答済みの自由入力だけを修正する。"""
    if question not in TEXT_LIMITS:
        raise QuestionnaireError("Only text answers can be edited")
    if getattr(progress, question) is None:
        raise QuestionnaireError("Question has not been answered")

    return replace(
        progress,
        **{question: _text_answer(question, value)},
    )
