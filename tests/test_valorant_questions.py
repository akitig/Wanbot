import json
from importlib.resources import files

import pytest

from wanbot.valorant.questions import (
    QuestionDataError,
    load_questions,
    parse_questions,
)


def raw_questions():
    resource = files("wanbot.data").joinpath("valorant_playstyle_questions.json")
    return json.loads(resource.read_text(encoding="utf-8"))


def test_current_question_set_loads():
    question_set = load_questions()

    assert question_set.schema_version == 1
    assert question_set.diagnosis_version == "2.0"
    assert len(question_set.questions) == 15
    assert [question.id for question in question_set.questions] == [
        f"q{number:02d}" for number in range(1, 16)
    ]
    assert all(len(question.choices) == 4 for question in question_set.questions)


def test_all_current_scores_are_preserved():
    expected = [
        ("win", [3, 0, 2, 1]),
        ("win", [2, 0, 3, 1]),
        ("team", [2, 0, 3, 1]),
        ("team", [3, 1, 2, 0]),
        ("team", [2, 0, 3, 1]),
        ("team", [1, 3, 0, 2]),
        ("improvement", [3, 0, 2, 1]),
        ("improvement", [1, 3, 0, 2]),
        ("improvement", [2, 0, 3, 1]),
        ("focus", [2, 3, 0, 1]),
        ("focus", [1, 3, 0, 2]),
        ("focus", [3, 0, 2, 1]),
        ("feedback_receive", [2, 3, 1, 0]),
        ("feedback_give", [3, 2, 0, 2]),
    ]
    questions = load_questions().questions

    for question, (axis, values) in zip(questions[:14], expected):
        assert [dict(choice.scores) for choice in question.choices] == [
            {axis: value} for value in values
        ]

    assert [dict(choice.scores) for choice in questions[14].choices] == [
        {"team": 3, "win": 3},
        {"team": 2, "win": 2},
        {"team": 0, "win": 0},
        {"team": 1, "win": 2},
    ]


def test_axis_maximums_match_current_specification():
    questions = load_questions().questions
    expected = {
        "win": 9,
        "team": 15,
        "improvement": 9,
        "focus": 9,
        "feedback_receive": 3,
        "feedback_give": 3,
    }

    for axis, maximum in expected.items():
        actual = sum(
            max(choice.scores.get(axis, 0) for choice in question.choices)
            for question in questions
        )
        assert actual == maximum


def test_missing_question_is_rejected():
    payload = raw_questions()
    payload["questions"].pop()

    with pytest.raises(QuestionDataError):
        parse_questions(payload)


def test_duplicate_question_id_is_rejected():
    payload = raw_questions()
    payload["questions"][1]["id"] = "q01"

    with pytest.raises(QuestionDataError):
        parse_questions(payload)


def test_missing_choice_is_rejected():
    payload = raw_questions()
    payload["questions"][0]["choices"].pop()

    with pytest.raises(QuestionDataError):
        parse_questions(payload)


def test_unknown_axis_is_rejected():
    payload = raw_questions()
    payload["questions"][0]["choices"][0]["scores"] = {"unknown": 3}

    with pytest.raises(QuestionDataError):
        parse_questions(payload)


@pytest.mark.parametrize("score", [-1, 4, True, 1.5])
def test_invalid_score_is_rejected(score):
    payload = raw_questions()
    payload["questions"][0]["choices"][0]["scores"] = {"win": score}

    with pytest.raises(QuestionDataError):
        parse_questions(payload)


def test_unknown_diagnosis_version_is_rejected():
    payload = raw_questions()
    payload["diagnosis_version"] = "999"

    with pytest.raises(QuestionDataError):
        parse_questions(payload)


def test_empty_question_text_is_rejected():
    payload = raw_questions()
    payload["questions"][0]["question"] = " "

    with pytest.raises(QuestionDataError):
        parse_questions(payload)


def test_loaded_scores_cannot_be_modified():
    choice = load_questions().questions[0].choices[0]

    with pytest.raises(TypeError):
        choice.scores["win"] = 99
