import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from wanbot.valorant.question_view import (
    AnswerSelection,
    QuestionView,
    format_question,
)
from wanbot.valorant.questions import load_questions
from wanbot.valorant.session import DiagnosisSession
from wanbot.valorant.session_data import session_from_data, session_to_data


START = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def make_session():
    return DiagnosisSession(
        guild_id=100,
        user_id=10,
        invoker_id=20,
        question_set=load_questions(),
        started_at=START,
        timeout_seconds=1800,
    )


def make_interaction(user_id=10, guild_id=None):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        guild_id=guild_id,
        response=SimpleNamespace(
            is_done=Mock(return_value=False),
            send_message=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def test_all_questions_fit_and_show_all_choices():
    session = make_session()

    for number in range(1, 16):
        question = session.current_question
        content = format_question(session)

        assert f"（{number} / 15）" in content
        assert question.text in content
        for choice in question.choices:
            assert f"**{choice.id.upper()}**：{choice.text}" in content
        assert "回答後の変更はできません" in content
        assert len(content) <= 2000

        session.submit(
            actor_id=10,
            question_id=question.id,
            choice_id="a",
            now=START + timedelta(seconds=number),
        )


def test_button_ids_are_stable_after_restore():
    async def scenario():
        session = make_session()
        restored = session_from_data(
            session_to_data(session),
            question_set=load_questions(),
            expected_guild_id=100,
            now=START,
        )
        first = QuestionView(session, AsyncMock())
        second = QuestionView(restored, AsyncMock())
        try:
            first_ids = [item.custom_id for item in first.children]
            second_ids = [item.custom_id for item in second.children]

            assert first.is_persistent()
            assert second.is_persistent()
            assert first_ids == second_ids
            assert len(set(first_ids)) == 4
            assert all(len(value) <= 100 for value in first_ids)
            assert [item.label for item in first.children] == ["A", "B", "C", "D"]
        finally:
            first.stop()
            second.stop()

    asyncio.run(scenario())


def test_other_user_cannot_trigger_answer_handler():
    async def scenario():
        handler = AsyncMock()
        view = QuestionView(make_session(), handler)
        interaction = make_interaction(user_id=99)
        try:
            await view.children[0].callback(interaction)

            handler.assert_not_awaited()
            interaction.response.send_message.assert_awaited_once()
            assert interaction.response.send_message.call_args.kwargs["ephemeral"]
        finally:
            view.stop()

    asyncio.run(scenario())


def test_other_guild_cannot_trigger_answer_handler():
    async def scenario():
        handler = AsyncMock()
        view = QuestionView(make_session(), handler)
        interaction = make_interaction(guild_id=200)
        try:
            await view.children[0].callback(interaction)

            handler.assert_not_awaited()
            interaction.response.send_message.assert_awaited_once()
        finally:
            view.stop()

    asyncio.run(scenario())


@pytest.mark.parametrize("guild_id", [None, 100])
def test_owner_can_answer_in_dm_or_original_guild(guild_id):
    async def scenario():
        handler = AsyncMock()
        session = make_session()
        view = QuestionView(session, handler)
        interaction = make_interaction(guild_id=guild_id)
        try:
            await view.children[0].callback(interaction)

            handler.assert_awaited_once_with(
                interaction,
                AnswerSelection(
                    guild_id=100,
                    user_id=10,
                    session_id=session.session_id,
                    question_id="q01",
                    choice_id="a",
                ),
            )
        finally:
            view.stop()

    asyncio.run(scenario())


def test_each_button_passes_its_own_choice():
    async def scenario():
        handler = AsyncMock()
        view = QuestionView(make_session(), handler)
        interaction = make_interaction()
        try:
            for button in view.children:
                await button.callback(interaction)

            choices = [
                call.args[1].choice_id
                for call in handler.await_args_list
            ]
            assert choices == ["a", "b", "c", "d"]
        finally:
            view.stop()

    asyncio.run(scenario())


@pytest.mark.parametrize("state", ["completed", "expired"])
def test_terminal_session_has_no_question_view(state):
    async def scenario():
        session = make_session()
        if state == "expired":
            session.expire_if_due(session.expires_at)
        else:
            for number in range(1, 16):
                session.submit(
                    actor_id=10,
                    question_id=f"q{number:02d}",
                    choice_id="a",
                    now=START + timedelta(seconds=number),
                )

        with pytest.raises(ValueError):
            format_question(session)
        with pytest.raises(ValueError):
            QuestionView(session, AsyncMock())

    asyncio.run(scenario())


def test_error_notice_does_not_expose_exception_body(monkeypatch):
    logger = Mock()
    monkeypatch.setattr(
        "wanbot.valorant.question_view.log_event",
        logger,
    )

    async def scenario():
        view = QuestionView(make_session(), AsyncMock())
        interaction = make_interaction()
        interaction.response.is_done.return_value = True
        try:
            await view.on_error(
                interaction,
                RuntimeError("PRIVATE_ERROR_BODY"),
                view.children[0],
            )

            logger.assert_called_once()
            interaction.followup.send.assert_awaited_once()
            content = interaction.followup.send.call_args.args[0]
            assert "PRIVATE_ERROR_BODY" not in content
            assert interaction.followup.send.call_args.kwargs["ephemeral"]
        finally:
            view.stop()

    asyncio.run(scenario())
