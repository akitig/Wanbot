"""現行VALORANT診断の質問データを読み込み、形式を検証する。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.resources import files
from types import MappingProxyType
from typing import Any


SUPPORTED_AXES = frozenset({
    "win",
    "team",
    "improvement",
    "focus",
    "feedback_receive",
    "feedback_give",
})

SCHEMA_VERSION = 1
DIAGNOSIS_VERSION = "2.0"
QUESTION_COUNT = 15
CHOICE_IDS = ("a", "b", "c", "d")


class QuestionDataError(ValueError):
    """質問マスターの不備。入力内容そのものはエラー文に含めない。"""


@dataclass(frozen=True)
class Choice:
    id: str
    text: str
    scores: Mapping[str, int]


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    choices: tuple[Choice, ...]


@dataclass(frozen=True)
class QuestionSet:
    schema_version: int
    diagnosis_version: str
    questions: tuple[Question, ...]


def _require_keys(value: Any, keys: set[str], location: str) -> None:
    if type(value) is not dict or set(value) != keys:
        raise QuestionDataError(f"{location}: 項目の構成が不正です。")


def _require_text(value: Any, location: str) -> str:
    if type(value) is not str or not value.strip():
        raise QuestionDataError(f"{location}: 空でない文字列が必要です。")
    return value


def parse_questions(payload: Any) -> QuestionSet:
    _require_keys(
        payload,
        {"schema_version", "diagnosis_version", "questions"},
        "root",
    )

    if (
        type(payload["schema_version"]) is not int
        or payload["schema_version"] != SCHEMA_VERSION
    ):
        raise QuestionDataError("未対応の質問ファイル形式です。")

    if payload["diagnosis_version"] != DIAGNOSIS_VERSION:
        raise QuestionDataError("未対応の診断バージョンです。")

    raw_questions = payload["questions"]
    if type(raw_questions) is not list or len(raw_questions) != QUESTION_COUNT:
        raise QuestionDataError("質問は15問である必要があります。")

    questions: list[Question] = []

    for number, raw_question in enumerate(raw_questions, start=1):
        location = f"q{number:02d}"
        _require_keys(raw_question, {"id", "question", "choices"}, location)

        if raw_question["id"] != location:
            raise QuestionDataError(f"{location}: 質問IDまたは並び順が不正です。")

        question_text = _require_text(raw_question["question"], location)
        raw_choices = raw_question["choices"]

        if type(raw_choices) is not list or len(raw_choices) != len(CHOICE_IDS):
            raise QuestionDataError(f"{location}: 選択肢は4つ必要です。")

        choices: list[Choice] = []

        for expected_id, raw_choice in zip(CHOICE_IDS, raw_choices):
            choice_location = f"{location}.{expected_id}"
            _require_keys(raw_choice, {"id", "text", "scores"}, choice_location)

            if raw_choice["id"] != expected_id:
                raise QuestionDataError(
                    f"{choice_location}: 選択肢IDまたは並び順が不正です。"
                )

            choice_text = _require_text(raw_choice["text"], choice_location)
            raw_scores = raw_choice["scores"]

            if type(raw_scores) is not dict or not raw_scores:
                raise QuestionDataError(f"{choice_location}: 配点が必要です。")

            scores: dict[str, int] = {}
            for axis, score in raw_scores.items():
                if axis not in SUPPORTED_AXES:
                    raise QuestionDataError(f"{choice_location}: 未対応の採点軸です。")
                if type(score) is not int or not 0 <= score <= 3:
                    raise QuestionDataError(
                        f"{choice_location}: 配点は0〜3の整数にしてください。"
                    )
                scores[axis] = score

            choices.append(
                Choice(
                    id=expected_id,
                    text=choice_text,
                    scores=MappingProxyType(scores),
                )
            )

        questions.append(
            Question(
                id=location,
                text=question_text,
                choices=tuple(choices),
            )
        )

    return QuestionSet(
        schema_version=SCHEMA_VERSION,
        diagnosis_version=DIAGNOSIS_VERSION,
        questions=tuple(questions),
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise QuestionDataError("質問ファイル内で項目名が重複しています。")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise QuestionDataError("質問ファイルに標準外の数値が含まれています。")


def load_questions() -> QuestionSet:
    resource = files("wanbot.data").joinpath("valorant_playstyle_questions.json")

    try:
        text = resource.read_text(encoding="utf-8")
        payload = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except QuestionDataError:
        raise
    except (OSError, ValueError, RecursionError):
        raise QuestionDataError("質問ファイルを読み込めません。") from None

    return parse_questions(payload)
