import pytest

from wanbot.valorant.questions import load_questions
from wanbot.valorant.scoring import (
    AnswerError,
    Category,
    ClassificationPolicy,
    DiagnosisScorer,
)


# 現行15問の選択肢ID。各軸で最大・最小になる回答。
MAX_CHOICES = (
    "a", "c", "c", "a", "c", "b", "a", "b",
    "c", "b", "b", "a", "b", "a", "a",
)
MIN_CHOICES = (
    "b", "b", "b", "d", "b", "c", "b", "c",
    "b", "c", "c", "b", "d", "c", "c",
)


def answers_from(choices):
    return {
        f"q{number:02d}": choice
        for number, choice in enumerate(choices, start=1)
    }


@pytest.fixture
def scorer():
    return DiagnosisScorer(load_questions())


def test_maximum_answers_are_gachi(scorer):
    result = scorer.evaluate(answers_from(MAX_CHOICES))

    assert result.category is Category.GACHI
    assert result.weighted_score == pytest.approx(1.0)
    assert result.axes["win"].score == 9
    assert result.axes["team"].score == 15
    assert result.axes["improvement"].score == 9
    assert result.axes["focus"].score == 9
    assert result.diagnosis_version == "2.0"


def test_minimum_answers_are_enjoy(scorer):
    result = scorer.evaluate(answers_from(MIN_CHOICES))

    assert result.category is Category.ENJOY
    assert result.weighted_score == 0
    assert all(axis.score == 0 for axis in result.axes.values())


def test_high_total_with_low_team_score_is_neutral(scorer):
    answers = answers_from(MAX_CHOICES)
    answers.update({
        "q03": "b",
        "q04": "d",
        "q05": "b",
        "q06": "c",
    })

    result = scorer.evaluate(answers)

    assert result.weighted_score > 0.65
    assert result.axes["team"].score == 3
    assert result.category is Category.NEUTRAL


def test_gachi_allows_exact_axis_minimums(scorer):
    answers = answers_from(MAX_CHOICES)
    answers.update({
        # チーム：Q3=0、Q4=0、Q5=3、Q6=3、Q15=3 → 9点
        "q03": "b",
        "q04": "d",
        # 改善：Q7=3、Q8=2、Q9=0 → 5点
        "q08": "d",
        "q09": "b",
        # 集中：Q10=3、Q11=2、Q12=0 → 5点
        "q11": "d",
        "q12": "b",
    })

    result = scorer.evaluate(answers)

    assert result.axes["team"].score == 9
    assert result.axes["improvement"].score == 5
    assert result.axes["focus"].score == 5
    assert result.weighted_score > 0.65
    assert result.category is Category.GACHI


def test_feedback_answers_do_not_change_classification(scorer):
    answers = answers_from(MAX_CHOICES)
    original = scorer.evaluate(answers)

    answers["q13"] = "d"
    answers["q14"] = "c"
    changed = scorer.evaluate(answers)

    assert changed.category is original.category
    assert changed.weighted_score == original.weighted_score
    assert changed.axes["feedback_receive"].score == 0
    assert changed.axes["feedback_give"].score == 0


def test_question_15_updates_both_team_and_win(scorer):
    answers = answers_from(MIN_CHOICES)
    answers["q15"] = "d"

    result = scorer.evaluate(answers)

    assert result.axes["team"].score == 1
    assert result.axes["win"].score == 2


def test_incomplete_answers_are_rejected(scorer):
    answers = answers_from(MAX_CHOICES)
    del answers["q15"]

    with pytest.raises(AnswerError):
        scorer.evaluate(answers)


def test_unknown_question_is_rejected(scorer):
    answers = answers_from(MAX_CHOICES)
    answers["q99"] = "a"

    with pytest.raises(AnswerError):
        scorer.evaluate(answers)


def test_unknown_choice_is_rejected(scorer):
    answers = answers_from(MAX_CHOICES)
    answers["q01"] = "z"

    with pytest.raises(AnswerError):
        scorer.evaluate(answers)


def test_non_string_choice_is_rejected(scorer):
    answers = answers_from(MAX_CHOICES)
    answers["q01"] = 1

    with pytest.raises(AnswerError):
        scorer.evaluate(answers)


def test_policy_can_be_overridden():
    scorer = DiagnosisScorer(
        load_questions(),
        policy=ClassificationPolicy(
            gachi_minimum=0.95,
            neutral_minimum=0.90,
        ),
    )
    answers = answers_from(MAX_CHOICES)
    answers.update({
        "q03": "b",
        "q04": "d",
        "q05": "b",
        "q06": "c",
    })

    result = scorer.evaluate(answers)

    assert result.weighted_score == pytest.approx(0.72)
    assert result.category is Category.ENJOY


@pytest.mark.parametrize("value", [-0.1, 1.1, float("nan"), True])
def test_invalid_policy_value_is_rejected(value):
    with pytest.raises(ValueError):
        ClassificationPolicy(gachi_minimum=value)


def test_reversed_thresholds_are_rejected():
    with pytest.raises(ValueError):
        ClassificationPolicy(
            gachi_minimum=0.30,
            neutral_minimum=0.50,
        )
