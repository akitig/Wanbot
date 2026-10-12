import pytest

from wanbot.welcome.questionnaire import (
    Questionnaire,
    QuestionnaireError,
    answer,
    edit_text_answer,
)


def before_age():
    progress = Questionnaire()
    progress = answer(progress, "name", "おおかみ")
    return answer(progress, "gender", "no_answer")


def completed(plays_valo="yes"):
    progress = answer(before_age(), "adult", "yes")
    progress = answer(progress, "main_game", "VALORANT")
    progress = answer(progress, "comment", "よろしくお願いします")
    progress = answer(progress, "hobby", "旅行")
    return answer(progress, "plays_valo", plays_valo)


def test_questions_follow_expected_order():
    progress = Questionnaire()
    responses = (
        ("name", "おおかみ"),
        ("gender", "female"),
        ("adult", "yes"),
        ("main_game", "VALORANT"),
        ("comment", "よろしくお願いします"),
        ("hobby", "旅行"),
        ("plays_valo", "yes"),
    )

    for question, value in responses:
        assert progress.next_question == question
        progress = answer(progress, question, value)

    assert progress.status == "completed"
    assert progress.next_question is None
    assert progress.needs_valorant_diagnosis is True


def test_no_valorant_skips_diagnosis():
    progress = completed("no")

    assert progress.status == "completed"
    assert progress.plays_valo is False
    assert progress.needs_valorant_diagnosis is False


def test_underage_requires_second_confirmation():
    progress = answer(before_age(), "adult", "no")

    assert progress.next_question == "age_confirmation"
    assert progress.status == "answering"
    assert progress.underage_confirmed is False

    progress = answer(progress, "age_confirmation", "under_25")

    assert progress.status == "age_review_required"
    assert progress.next_question is None
    assert progress.main_game is None
    assert progress.needs_valorant_diagnosis is False


def test_age_mistake_can_be_corrected_at_confirmation():
    progress = answer(before_age(), "adult", "no")
    progress = answer(
        progress, "age_confirmation", "adult_correction"
    )

    assert progress.adult is True
    assert progress.underage_confirmed is False
    assert progress.next_question == "main_game"


def test_cannot_skip_question():
    with pytest.raises(QuestionnaireError):
        answer(Questionnaire(), "adult", "yes")


def test_old_question_cannot_be_submitted_again():
    progress = answer(Questionnaire(), "name", "おおかみ")

    with pytest.raises(QuestionnaireError):
        answer(progress, "name", "変更後")


@pytest.mark.parametrize("value", ["", "   ", "\n\t", "\u200b"])
def test_empty_or_invisible_name_is_rejected(value):
    with pytest.raises(QuestionnaireError):
        answer(Questionnaire(), "name", value)


def test_text_length_limit_is_checked():
    accepted = answer(Questionnaire(), "name", "あ" * 100)
    assert accepted.name == "あ" * 100

    with pytest.raises(QuestionnaireError):
        answer(Questionnaire(), "name", "あ" * 101)


def test_control_character_is_rejected():
    with pytest.raises(QuestionnaireError):
        answer(Questionnaire(), "name", "名前\x00")


@pytest.mark.parametrize("gender", ["female", "male", "no_answer"])
def test_gender_choices(gender):
    progress = answer(Questionnaire(), "name", "おおかみ")
    progress = answer(progress, "gender", gender)

    assert progress.gender == gender


def test_invalid_gender_is_rejected():
    progress = answer(Questionnaire(), "name", "おおかみ")

    with pytest.raises(QuestionnaireError):
        answer(progress, "gender", "invalid")


def test_invalid_yes_no_is_rejected():
    with pytest.raises(QuestionnaireError):
        answer(before_age(), "adult", "maybe")


def test_invalid_age_confirmation_is_rejected():
    progress = answer(before_age(), "adult", "no")

    with pytest.raises(QuestionnaireError):
        answer(progress, "age_confirmation", "invalid")


def test_completed_answers_cannot_be_submitted_again():
    with pytest.raises(QuestionnaireError):
        answer(completed(), "plays_valo", "no")


def test_underage_review_stops_questions():
    progress = answer(before_age(), "adult", "no")
    progress = answer(progress, "age_confirmation", "under_25")

    with pytest.raises(QuestionnaireError):
        answer(progress, "main_game", "VALORANT")


def test_answer_does_not_modify_original_state():
    original = Questionnaire()
    updated = answer(original, "name", "  おおかみ  ")

    assert original.name is None
    assert updated.name == "おおかみ"


def test_answered_text_can_be_edited_after_completion():
    original = completed()
    updated = edit_text_answer(original, "hobby", "読書")

    assert original.hobby == "旅行"
    assert updated.hobby == "読書"
    assert updated.status == "completed"
    assert updated.plays_valo == original.plays_valo


def test_unanswered_text_cannot_be_edited():
    with pytest.raises(QuestionnaireError):
        edit_text_answer(Questionnaire(), "hobby", "読書")


@pytest.mark.parametrize("question", ["gender", "adult", "plays_valo"])
def test_selection_fields_cannot_be_edited_as_text(question):
    with pytest.raises(QuestionnaireError):
        edit_text_answer(completed(), question, "変更")
