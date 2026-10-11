import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from wanbot.welcome.questionnaire import Questionnaire, answer
from wanbot.welcome.questionnaire_forms import (
    QuestionnaireContext,
    QuestionnaireStartView,
    QuestionView,
    TextQuestionModal,
)
from wanbot.welcome.questionnaire_repository import QuestionnaireSnapshot


def context():
    return QuestionnaireContext(
        guild_id=100,
        channel_id=500,
        user_id=300,
        session_id="a" * 32,
        is_available=Mock(return_value=True),
    )


def interaction():
    return SimpleNamespace(
        guild_id=100,
        channel_id=500,
        user=SimpleNamespace(id=300),
        response=SimpleNamespace(
            is_done=Mock(return_value=False),
            send_message=AsyncMock(),
            send_modal=AsyncMock(),
            defer=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def snapshot(progress=None, revision=0):
    return QuestionnaireSnapshot(
        session_id="a" * 32,
        user_id=300,
        revision=revision,
        progress=progress if progress is not None else Questionnaire(),
    )


def test_start_button_is_persistent():
    async def run():
        view = QuestionnaireStartView(context(), on_begin=AsyncMock())
        try:
            assert view.is_persistent()
            assert view.children[0].custom_id.endswith("a" * 32)
        finally:
            view.stop()

    asyncio.run(run())


def test_owner_can_start():
    async def run():
        callback = AsyncMock()
        view = QuestionnaireStartView(context(), on_begin=callback)
        item = interaction()
        try:
            await view.begin(item)

            item.response.defer.assert_awaited_once_with(
                ephemeral=True, thinking=True
            )
            callback.assert_awaited_once_with(item)
        finally:
            view.stop()

    asyncio.run(run())


@pytest.mark.parametrize(
    "case",
    ["other_user", "other_guild", "other_channel", "unavailable"],
)
def test_invalid_context_cannot_start(case):
    async def run():
        ctx = context()
        item = interaction()
        callback = AsyncMock()

        if case == "other_user":
            item.user.id = 301
        elif case == "other_guild":
            item.guild_id = 999
        elif case == "other_channel":
            item.channel_id = 999
        else:
            ctx.is_available.return_value = False

        view = QuestionnaireStartView(ctx, on_begin=callback)
        try:
            await view.begin(item)

            callback.assert_not_awaited()
            item.response.send_message.assert_awaited_once()
            assert item.response.send_message.call_args.kwargs["ephemeral"]
        finally:
            view.stop()

    asyncio.run(run())


def test_text_question_opens_modal():
    async def run():
        view = QuestionView(
            context(), snapshot(), on_answer=AsyncMock()
        )
        item = interaction()
        modal = None
        try:
            await view.open_modal(item)

            modal = item.response.send_modal.call_args.args[0]
            assert isinstance(modal, TextQuestionModal)
            assert modal.question == "name"
            assert modal.revision == 0
            assert modal.answer_input.max_length == 100
        finally:
            if modal is not None:
                modal.stop()
            view.stop()

    asyncio.run(run())


def test_modal_rechecks_owner_before_saving():
    async def run():
        callback = AsyncMock()
        modal = TextQuestionModal(
            context(),
            question="name",
            revision=0,
            on_answer=callback,
        )
        item = interaction()
        item.user.id = 301
        try:
            await modal.on_submit(item)

            callback.assert_not_awaited()
            item.response.defer.assert_not_awaited()
            item.response.send_message.assert_awaited_once()
        finally:
            modal.stop()

    asyncio.run(run())


def test_modal_passes_answer_and_revision():
    async def run():
        callback = AsyncMock()
        modal = TextQuestionModal(
            context(),
            question="name",
            revision=4,
            on_answer=callback,
        )
        modal.answer_input._value = "おおかみ"
        item = interaction()
        try:
            await modal.on_submit(item)

            callback.assert_awaited_once_with(
                item, "name", "おおかみ", 4
            )
        finally:
            modal.stop()

    asyncio.run(run())


def test_gender_menu_has_three_choices():
    async def run():
        progress = answer(Questionnaire(), "name", "おおかみ")
        view = QuestionView(
            context(),
            snapshot(progress, revision=1),
            on_answer=AsyncMock(),
        )
        try:
            assert {
                option.value for option in view.selection.options
            } == {"female", "male", "no_answer"}
            assert view.selection.max_values == 1
        finally:
            view.stop()

    asyncio.run(run())


def test_selection_passes_revision():
    async def run():
        callback = AsyncMock()
        progress = answer(Questionnaire(), "name", "おおかみ")
        view = QuestionView(
            context(),
            snapshot(progress, revision=1),
            on_answer=callback,
        )
        view.selection._values = ["no_answer"]
        item = interaction()
        try:
            await view.select_answer(item)

            callback.assert_awaited_once_with(
                item, "gender", "no_answer", 1
            )
            assert view.is_finished()
        finally:
            view.stop()

    asyncio.run(run())


def test_invalid_selection_is_not_saved():
    async def run():
        callback = AsyncMock()
        progress = answer(Questionnaire(), "name", "おおかみ")
        view = QuestionView(
            context(),
            snapshot(progress, revision=1),
            on_answer=callback,
        )
        view.selection._values = ["invalid"]
        try:
            await view.select_answer(interaction())
            callback.assert_not_awaited()
        finally:
            view.stop()

    asyncio.run(run())


def test_wrong_snapshot_owner_is_rejected():
    async def run():
        wrong = QuestionnaireSnapshot(
            session_id="a" * 32,
            user_id=301,
            revision=0,
            progress=Questionnaire(),
        )

        with pytest.raises(ValueError):
            QuestionView(context(), wrong, on_answer=AsyncMock())

    asyncio.run(run())
