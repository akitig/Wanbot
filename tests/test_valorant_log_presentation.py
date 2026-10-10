from copy import deepcopy
from dataclasses import asdict, replace

import pytest

from wanbot.valorant.log_presentation import format_log_pages
from wanbot.valorant.presentation import format_result
from wanbot.valorant.questions import load_questions
from wanbot.valorant.scoring import ClassificationPolicy, DiagnosisScorer


def make_record():
    questions = load_questions()
    answers = {
        question.id: question.choices[0].id
        for question in questions.questions
    }
    policy = ClassificationPolicy()
    result = DiagnosisScorer(questions, policy=policy).evaluate(answers)

    return {
        "record_version": 1,
        "user_id": 10,
        "invoker_id": 20,
        "completed_at": "2026-10-11T12:00:00+00:00",
        "diagnosis_version": result.diagnosis_version,
        "answers": answers,
        "policy": asdict(policy),
        "category": result.category.value,
        "weighted_score": result.weighted_score,
        "axes": {
            name: asdict(score)
            for name, score in result.axes.items()
        },
    }


def test_summary_contains_identity_time_and_scores():
    pages = format_log_pages(make_record(), load_questions())

    assert "回答者ID：10" in pages[0]
    assert "依頼した管理者ID：20" in pages[0]
    assert "2026-10-11 21:00:00（日本時間）" in pages[0]
    assert "総合スコア：" in pages[0]
    assert "フィードバック傾向" in pages[0]


def test_all_fifteen_questions_and_selected_answers_are_present():
    questions = load_questions()
    record = make_record()
    pages = format_log_pages(record, questions)
    text = "\n".join(pages[1:])

    for number, question in enumerate(questions.questions, start=1):
        marker = f"Q{number:02d}．"
        selected = question.choices[0]

        assert text.count(marker) == 1
        assert question.text in text
        assert f"回答 {selected.id.upper()}：{selected.text}" in text


def test_pages_fit_discord_message_limit():
    pages = format_log_pages(make_record(), load_questions())

    for number, page in enumerate(pages, start=1):
        assert len(page) <= 2000
        assert page.endswith(f"ページ {number} / {len(pages)}")


def test_each_answer_page_has_at_most_five_questions():
    pages = format_log_pages(make_record(), load_questions())

    for page in pages[1:]:
        count = sum(
            f"Q{number:02d}．" in page
            for number in range(1, 16)
        )
        assert 1 <= count <= 5


def test_saved_classification_policy_is_used():
    questions = load_questions()
    record = make_record()
    saved_policy = ClassificationPolicy(
        gachi_minimum=0.99,
        neutral_minimum=0.98,
    )
    record["policy"] = asdict(saved_policy)
    result = DiagnosisScorer(
        questions,
        policy=saved_policy,
    ).evaluate(record["answers"])
    record["category"] = result.category.value

    pages = format_log_pages(record, questions)

    assert format_result(result, include_total=True) in pages[0]


def test_rendering_does_not_change_saved_data():
    record = make_record()
    before = deepcopy(record)

    format_log_pages(record, load_questions())

    assert record == before


def test_other_question_version_is_rejected():
    record = make_record()
    record["diagnosis_version"] = "unknown"

    with pytest.raises(ValueError):
        format_log_pages(record, load_questions())


def test_long_questions_are_split_without_truncation():
    questions = load_questions()
    expanded = replace(
        questions,
        questions=tuple(
            replace(question, text=f"{question.id} " + "長い質問" * 100)
            for question in questions.questions
        ),
    )

    pages = format_log_pages(make_record(), expanded)

    assert len(pages) > 4
    assert all(len(page) <= 2000 for page in pages)
    text = "\n".join(pages)
    for question in expanded.questions:
        assert question.text in text
