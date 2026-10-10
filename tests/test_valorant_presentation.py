from wanbot.valorant.presentation import INTRODUCTION, format_result
from wanbot.valorant.questions import load_questions
from wanbot.valorant.scoring import DiagnosisScorer

MAX_CHOICES = (
    "a", "c", "c", "a", "c",
    "b", "a", "b", "c", "b",
    "b", "a", "b", "a", "a",
)

MIN_CHOICES = (
    "b", "b", "b", "d", "b",
    "c", "b", "c", "b", "c",
    "c", "b", "d", "c", "c",
)


def make_answers(choices):
    return {
        f"q{number:02d}": choice
        for number, choice in enumerate(choices, start=1)
    }


def evaluate(choices, changes=None):
    answers = make_answers(choices)
    if changes:
        answers.update(changes)
    return DiagnosisScorer(load_questions()).evaluate(answers)


def test_gachi_result_shows_four_axes_without_total_by_default():
    text = format_result(evaluate(MAX_CHOICES))

    assert text.startswith("🔥 ガチ\n")
    for label in ("🏆 勝利志向", "🤝 チーム志向", "🔧 改善志向", "🎯 集中志向"):
        assert label in text
    assert text.count("██████████ 100%") == 4
    assert "総合スコア" not in text
    assert "実力やランクを評価するものではありません" in text


def test_enjoy_result_shows_empty_bars():
    text = format_result(evaluate(MIN_CHOICES))

    assert text.startswith("🥳 エンジョイ\n")
    assert text.count("░░░░░░░░░░ 0%") == 4


def test_neutral_result_uses_existing_display_name():
    result = evaluate(
        MAX_CHOICES,
        {"q03": "b", "q04": "d", "q05": "b", "q06": "c"},
    )

    text = format_result(result, include_total=True)

    assert text.startswith("⚖️ 中立\n")
    assert "総合スコア：72%" in text
    assert "🤝 チーム志向\n██░░░░░░░░ 20%" in text


def test_admin_display_can_include_total():
    text = format_result(evaluate(MAX_CHOICES), include_total=True)

    assert "総合スコア：100%" in text


def test_feedback_answers_change_feedback_description():
    result = evaluate(MAX_CHOICES, {"q13": "d", "q14": "c"})

    text = format_result(result)

    assert text.startswith("🔥 ガチ\n")
    assert "基本的には自分で考えてプレイすることを好む" in text
    assert "基本的には相手のプレイ判断を尊重する" in text


def test_introduction_explains_purpose_and_question_count():
    assert "上手い・下手" in INTRODUCTION
    assert "普段どう遊びたいかで答えてください" in INTRODUCTION
    assert "全15問です。" in INTRODUCTION
