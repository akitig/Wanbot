import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from wanbot.config import parse_config
from wanbot.welcome.questionnaire_forms import EditAnswersView
from wanbot.welcome.questionnaire_controller import QuestionnaireController
from wanbot.welcome.questionnaire_forms import QuestionnaireContext
from wanbot.welcome.questionnaire_repository import QuestionnaireRepository
from wanbot.welcome.repository import WelcomeRepository


async def make_case(tmp_path):
    config = parse_config({
        "DISCORD_TOKEN": "test-token",
        "PROD_GUILD_ID": "101",
        "TEST_GUILD_ID": "100",
        "RUNTIME_DATA_DIR": str(tmp_path / "runtime"),
    })
    welcome = WelcomeRepository(config, 100)
    questions = QuestionnaireRepository(config, 100)
    session = await welcome.begin(300)
    session = await welcome.attach_channel(session.session_id, 500)

    context = QuestionnaireContext(
        guild_id=100,
        channel_id=500,
        user_id=300,
        session_id=session.session_id,
        is_available=Mock(return_value=True),
    )
    lock = asyncio.Lock()
    controller = QuestionnaireController(
        context=context,
        welcome_repository=welcome,
        questionnaire_repository=questions,
        lock=lock,
    )
    interaction = SimpleNamespace(
        guild_id=100,
        channel_id=500,
        user=SimpleNamespace(id=300),
        response=SimpleNamespace(
            is_done=Mock(return_value=True),
            send_message=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )
    return SimpleNamespace(
        welcome=welcome,
        questions=questions,
        session=session,
        context=context,
        lock=lock,
        controller=controller,
        interaction=interaction,
    )


async def close_case(item):
    item.controller.close()
    await item.questions.close()
    await item.welcome.close()


def test_begin_and_answer_advance_after_saving(tmp_path):
    async def run():
        item = await make_case(tmp_path)
        try:
            await item.controller.begin(item.interaction)
            view = item.interaction.edit_original_response.call_args.kwargs["view"]
            assert view.question == "name"

            await item.controller.answer(
                item.interaction, "name", "おおかみ", 0
            )

            saved = await item.questions.get(item.session.session_id)
            assert saved.progress.name == "おおかみ"
            view = item.interaction.edit_original_response.call_args.kwargs["view"]
            assert view.question == "gender"
            assert view.revision == 1
        finally:
            await close_case(item)

    asyncio.run(run())


def test_recreated_controller_resumes_saved_question(tmp_path):
    async def run():
        item = await make_case(tmp_path)
        try:
            await item.controller.begin(item.interaction)
            await item.controller.answer(
                item.interaction, "name", "おおかみ", 0
            )
            item.controller.close()
            item.controller = QuestionnaireController(
                context=item.context,
                welcome_repository=item.welcome,
                questionnaire_repository=item.questions,
                lock=item.lock,
            )

            await item.controller.begin(item.interaction)

            view = item.interaction.edit_original_response.call_args.kwargs["view"]
            assert view.question == "gender"
            assert view.revision == 1
        finally:
            await close_case(item)

    asyncio.run(run())


def test_stale_answer_does_not_replace_saved_answer(tmp_path):
    async def run():
        item = await make_case(tmp_path)
        try:
            await item.controller.begin(item.interaction)
            await item.controller.answer(
                item.interaction, "name", "おおかみ", 0
            )
            await item.controller.answer(
                item.interaction, "name", "別の名前", 0
            )

            saved = await item.questions.get(item.session.session_id)
            assert saved.progress.name == "おおかみ"
            assert saved.revision == 1
            message = item.interaction.edit_original_response.call_args.kwargs
            assert "状態が変わりました" in message["content"]
            assert message["view"] is None
        finally:
            await close_case(item)

    asyncio.run(run())


def test_archiving_session_cannot_accept_answers(tmp_path):
    async def run():
        item = await make_case(tmp_path)
        try:
            await item.controller.begin(item.interaction)
            await item.welcome.begin_archive(
                item.session.session_id, reason="member_left"
            )

            await item.controller.answer(
                item.interaction, "name", "おおかみ", 0
            )

            saved = await item.questions.get(item.session.session_id)
            assert saved.revision == 0
            assert saved.progress.name is None
        finally:
            await close_case(item)

    asyncio.run(run())


def test_wrong_user_cannot_initialize_answers(tmp_path):
    async def run():
        item = await make_case(tmp_path)
        try:
            item.interaction.user.id = 301

            await item.controller.begin(item.interaction)

            assert await item.questions.get(item.session.session_id) is None
            item.interaction.followup.send.assert_awaited_once()
        finally:
            await close_case(item)

    asyncio.run(run())


def test_save_failure_does_not_advance(tmp_path):
    async def run():
        item = await make_case(tmp_path)
        try:
            await item.controller.begin(item.interaction)
            item.questions.submit = AsyncMock(
                side_effect=OSError("test failure")
            )

            await item.controller.answer(
                item.interaction, "name", "おおかみ", 0
            )

            saved = await item.questions.get(item.session.session_id)
            assert saved.progress.next_question == "name"
            assert saved.revision == 0
            assert (
                item.interaction.edit_original_response.call_args.kwargs["view"]
                is None
            )
        finally:
            await close_case(item)

    asyncio.run(run())


def test_all_answers_show_summary_with_edit_menu(tmp_path):
    async def run():
        item = await make_case(tmp_path)
        try:
            await item.controller.begin(item.interaction)
            responses = (
                ("name", "おおかみ"),
                ("gender", "no_answer"),
                ("adult", "yes"),
                ("main_game", "VALORANT"),
                ("comment", "よろしくお願いします"),
                ("hobby", "旅行"),
                ("plays_valo", "yes"),
            )
            for revision, (question, value) in enumerate(responses):
                await item.controller.answer(
                    item.interaction, question, value, revision
                )

            saved = await item.questions.get(item.session.session_id)
            assert saved.progress.status == "completed"
            result = item.interaction.edit_original_response.call_args.kwargs
            assert isinstance(result["view"], EditAnswersView)
            assert len(result["embed"].fields) == 7
            assert result["embed"].fields[0].value == "おおかみ"
        finally:
            await close_case(item)

    asyncio.run(run())


def test_underage_confirmation_stops_questions(tmp_path):
    async def run():
        item = await make_case(tmp_path)
        try:
            await item.controller.begin(item.interaction)
            responses = (
                ("name", "おおかみ"),
                ("gender", "no_answer"),
                ("adult", "no"),
                ("age_confirmation", "under_25"),
            )
            for revision, (question, value) in enumerate(responses):
                await item.controller.answer(
                    item.interaction, question, value, revision
                )

            saved = await item.questions.get(item.session.session_id)
            assert saved.progress.status == "age_review_required"
            result = item.interaction.edit_original_response.call_args.kwargs
            assert isinstance(result["view"], EditAnswersView)
            assert "運営からの案内" in result["content"]
        finally:
            await close_case(item)

    asyncio.run(run())


def test_completion_is_shared_and_reopen_retries_sharing(tmp_path):
    async def run():
        item = await make_case(tmp_path)
        shared = AsyncMock()
        item.controller._on_completed = shared
        try:
            await item.controller.begin(item.interaction)
            responses = (
                ("name", "おおかみ"),
                ("gender", "no_answer"),
                ("adult", "yes"),
                ("main_game", "VALORANT"),
                ("comment", "よろしくお願いします"),
                ("hobby", "旅行"),
                ("plays_valo", "no"),
            )
            for revision, (question, value) in enumerate(responses):
                await item.controller.answer(
                    item.interaction, question, value, revision
                )

            shared.assert_awaited_once()
            assert shared.call_args.args[0].progress.status == "completed"

            await item.controller.begin(item.interaction)
            assert shared.await_count == 2
        finally:
            await close_case(item)

    asyncio.run(run())


async def complete_for_edit_test(item):
    await item.controller.begin(item.interaction)
    responses = (
        ("name", "おおかみ"),
        ("gender", "no_answer"),
        ("adult", "yes"),
        ("main_game", "VALORANT"),
        ("comment", "よろしくお願いします"),
        ("hobby", "旅行"),
        ("plays_valo", "yes"),
    )
    for revision, (question, value) in enumerate(responses):
        await item.controller.answer(
            item.interaction, question, value, revision
        )


def test_edit_updates_saved_answer_and_shared_result(tmp_path):
    async def run():
        item = await make_case(tmp_path)
        shared = AsyncMock()
        item.controller._on_completed = shared
        try:
            await complete_for_edit_test(item)
            shared.reset_mock()

            await item.controller.edit_text(
                item.interaction, "hobby", "読書", 7
            )

            saved = await item.questions.get(item.session.session_id)
            assert saved.revision == 8
            assert saved.progress.hobby == "読書"
            assert saved.progress.gender == "no_answer"
            assert saved.progress.plays_valo is True

            shared.assert_awaited_once_with(saved)
            result = item.interaction.edit_original_response.call_args.kwargs
            assert isinstance(result["view"], EditAnswersView)
            assert next(
                field.value
                for field in result["embed"].fields
                if field.name == "リアルの趣味"
            ) == "読書"
        finally:
            await close_case(item)

    asyncio.run(run())


def test_stale_edit_does_not_overwrite_newer_answer(tmp_path):
    async def run():
        item = await make_case(tmp_path)
        try:
            await complete_for_edit_test(item)
            await item.controller.edit_text(
                item.interaction, "hobby", "読書", 7
            )
            await item.controller.edit_text(
                item.interaction, "hobby", "古い画面からの回答", 7
            )

            saved = await item.questions.get(item.session.session_id)
            assert saved.revision == 8
            assert saved.progress.hobby == "読書"
        finally:
            await close_case(item)

    asyncio.run(run())


def test_edit_during_questions_preserves_next_question(tmp_path):
    async def run():
        item = await make_case(tmp_path)
        try:
            await item.controller.begin(item.interaction)
            await item.controller.answer(
                item.interaction, "name", "おおかみ", 0
            )
            await item.controller.show_edit_menu(item.interaction)

            shown = item.interaction.edit_original_response.call_args.kwargs["view"]
            assert {
                option.value for option in shown.selection.options
            } == {"name"}

            await item.controller.edit_text(
                item.interaction, "name", "わんこ", 1
            )

            saved = await item.questions.get(item.session.session_id)
            assert saved.progress.name == "わんこ"
            assert saved.progress.next_question == "gender"
            shown = item.interaction.edit_original_response.call_args.kwargs["view"]
            assert shown.question == "gender"
        finally:
            await close_case(item)

    asyncio.run(run())


def test_edit_menu_opens_form_with_current_answer(tmp_path):
    async def run():
        item = await make_case(tmp_path)
        modal = None
        try:
            await complete_for_edit_test(item)
            view = item.interaction.edit_original_response.call_args.kwargs["view"]
            assert {
                option.value for option in view.selection.options
            } == {"name", "main_game", "comment", "hobby"}

            view.selection._values = ["hobby"]
            item.interaction.response.send_modal = AsyncMock()

            await view.open_edit_modal(item.interaction)

            modal = item.interaction.response.send_modal.call_args.args[0]
            assert modal.answer_input.default == "旅行"
            assert modal.revision == 7
            assert modal.question == "hobby"
        finally:
            if modal is not None:
                modal.stop()
            await close_case(item)

    asyncio.run(run())


def test_archiving_session_cannot_edit_answers(tmp_path):
    async def run():
        item = await make_case(tmp_path)
        try:
            await complete_for_edit_test(item)
            await item.welcome.begin_archive(
                item.session.session_id, reason="member_left"
            )

            await item.controller.edit_text(
                item.interaction, "hobby", "読書", 7
            )

            saved = await item.questions.get(item.session.session_id)
            assert saved.revision == 7
            assert saved.progress.hobby == "旅行"
        finally:
            await close_case(item)

    asyncio.run(run())


def test_other_user_cannot_edit_answers(tmp_path):
    async def run():
        item = await make_case(tmp_path)
        try:
            await complete_for_edit_test(item)
            item.interaction.user.id = 301

            await item.controller.edit_text(
                item.interaction, "hobby", "読書", 7
            )

            saved = await item.questions.get(item.session.session_id)
            assert saved.revision == 7
            assert saved.progress.hobby == "旅行"
        finally:
            await close_case(item)

    asyncio.run(run())


def test_selection_answer_cannot_be_edited_as_text(tmp_path):
    async def run():
        item = await make_case(tmp_path)
        try:
            await complete_for_edit_test(item)

            await item.controller.edit_text(
                item.interaction, "plays_valo", "no", 7
            )

            saved = await item.questions.get(item.session.session_id)
            assert saved.revision == 7
            assert saved.progress.plays_valo is True
        finally:
            await close_case(item)

    asyncio.run(run())
