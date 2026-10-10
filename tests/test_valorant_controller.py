import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from wanbot.storage.json_store import StorageError
from wanbot.valorant.controller import DiagnosisController
from wanbot.valorant.question_view import AnswerSelection, QuestionView
from wanbot.valorant.questions import load_questions
from wanbot.valorant.scoring import DiagnosisScorer
from wanbot.valorant.service import DiagnosisProgress
from wanbot.valorant.session import DiagnosisSession, SessionError


START = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
NOW = START + timedelta(minutes=1)


def make_session():
    return DiagnosisSession(
        guild_id=100,
        user_id=10,
        invoker_id=20,
        question_set=load_questions(),
        started_at=START,
        timeout_seconds=1800,
    )


def answer_session(session, number):
    session.submit(
        actor_id=10,
        question_id=f"q{number:02d}",
        choice_id="a",
        now=NOW,
    )


def make_selection(session, guild_id=100):
    return AnswerSelection(
        guild_id=guild_id,
        user_id=10,
        session_id=session.session_id,
        question_id="q01",
        choice_id="a",
    )


def make_interaction(user_id=10, guild_id=None):
    response = SimpleNamespace(
        is_done=Mock(return_value=False),
        send_message=AsyncMock(),
    )

    async def defer(**kwargs):
        response.is_done.return_value = True

    response.defer = AsyncMock(side_effect=defer)

    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        guild_id=guild_id,
        response=response,
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


def make_controller(service, available=None):
    return DiagnosisController(
        100,
        service,
        is_available=available if available is not None else Mock(return_value=True),
        clock=lambda: NOW,
    )


def test_next_question_is_displayed_after_saving():
    async def scenario():
        session = make_session()
        selection = make_selection(session)
        answer_session(session, 1)
        events = []

        async def save_answer(**kwargs):
            events.append("saved")
            return DiagnosisProgress(session=session)

        async def edit_message(**kwargs):
            events.append("displayed")

        service = SimpleNamespace(answer=AsyncMock(side_effect=save_answer))
        controller = make_controller(service)
        interaction = make_interaction()
        interaction.edit_original_response.side_effect = edit_message

        await controller.on_answer(interaction, selection)

        assert events == ["saved", "displayed"]
        interaction.response.defer.assert_awaited_once_with(thinking=False)
        service.answer.assert_awaited_once_with(
            user_id=10,
            session_id=session.session_id,
            actor_id=10,
            question_id="q01",
            choice_id="a",
            now=NOW,
        )

        options = interaction.edit_original_response.call_args.kwargs
        assert "（2 / 15）" in options["content"]
        assert isinstance(options["view"], QuestionView)
        assert options["allowed_mentions"].to_dict() == {"parse": []}
        options["view"].stop()

    asyncio.run(scenario())


def test_completed_result_removes_buttons():
    async def scenario():
        session = make_session()
        for number in range(1, 16):
            answer_session(session, number)

        result = DiagnosisScorer(load_questions()).evaluate(session.answers)
        service = SimpleNamespace(
            answer=AsyncMock(
                return_value=DiagnosisProgress(session=session, result=result)
            )
        )
        controller = make_controller(service)
        interaction = make_interaction()

        await controller.on_answer(interaction, make_selection(session))

        options = interaction.edit_original_response.call_args.kwargs
        assert options["view"] is None
        assert "フィードバック傾向" in options["content"]
        assert "総合スコア" not in options["content"]

    asyncio.run(scenario())


@pytest.mark.parametrize("mismatch", ["user", "guild", "selection"])
def test_wrong_scope_does_not_call_service(mismatch):
    async def scenario():
        session = make_session()
        selection = make_selection(
            session,
            guild_id=200 if mismatch == "selection" else 100,
        )
        interaction = make_interaction(
            user_id=99 if mismatch == "user" else 10,
            guild_id=200 if mismatch == "guild" else None,
        )
        service = SimpleNamespace(answer=AsyncMock())
        controller = make_controller(service)

        await controller.on_answer(interaction, selection)

        service.answer.assert_not_awaited()
        interaction.edit_original_response.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once()

    asyncio.run(scenario())


def test_disabled_feature_does_not_call_service():
    async def scenario():
        service = SimpleNamespace(answer=AsyncMock())
        controller = make_controller(service, Mock(return_value=False))
        interaction = make_interaction()

        await controller.on_answer(
            interaction,
            make_selection(make_session()),
        )

        service.answer.assert_not_awaited()
        interaction.response.defer.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once()

    asyncio.run(scenario())


def test_feature_stopped_after_acceptance_does_not_call_service():
    async def scenario():
        service = SimpleNamespace(answer=AsyncMock())
        available = Mock(side_effect=[True, False])
        controller = make_controller(service, available)
        interaction = make_interaction()

        await controller.on_answer(
            interaction,
            make_selection(make_session()),
        )

        interaction.response.defer.assert_awaited_once()
        service.answer.assert_not_awaited()
        interaction.followup.send.assert_awaited_once()

    asyncio.run(scenario())


def test_rejected_answer_does_not_change_question():
    async def scenario():
        service = SimpleNamespace(
            answer=AsyncMock(side_effect=SessionError("PRIVATE_DETAIL"))
        )
        controller = make_controller(service)
        interaction = make_interaction()

        await controller.on_answer(
            interaction,
            make_selection(make_session()),
        )

        interaction.edit_original_response.assert_not_awaited()
        content = interaction.followup.send.call_args.args[0]
        assert "受け付けられません" in content
        assert "PRIVATE_DETAIL" not in content

    asyncio.run(scenario())


def test_storage_failure_does_not_advance_display(monkeypatch):
    monkeypatch.setattr("wanbot.valorant.controller.log_event", Mock())

    async def scenario():
        service = SimpleNamespace(
            answer=AsyncMock(side_effect=StorageError("PRIVATE_DETAIL"))
        )
        controller = make_controller(service)
        interaction = make_interaction()

        await controller.on_answer(
            interaction,
            make_selection(make_session()),
        )

        interaction.edit_original_response.assert_not_awaited()
        content = interaction.followup.send.call_args.args[0]
        assert "保存処理を完了できませんでした" in content
        assert "PRIVATE_DETAIL" not in content

    asyncio.run(scenario())


def test_display_failure_does_not_submit_answer_again(monkeypatch):
    monkeypatch.setattr("wanbot.valorant.controller.log_event", Mock())

    async def scenario():
        session = make_session()
        selection = make_selection(session)
        answer_session(session, 1)
        service = SimpleNamespace(
            answer=AsyncMock(return_value=DiagnosisProgress(session=session))
        )
        controller = make_controller(service)
        interaction = make_interaction()
        interaction.edit_original_response.side_effect = discord.HTTPException(
            SimpleNamespace(status=500, reason="Server Error"),
            "PRIVATE_DETAIL",
        )

        await controller.on_answer(interaction, selection)

        service.answer.assert_awaited_once()
        content = interaction.followup.send.call_args.args[0]
        assert "回答は保存しました" in content
        assert "PRIVATE_DETAIL" not in content

        view = interaction.edit_original_response.call_args.kwargs["view"]
        assert view.is_finished()

    asyncio.run(scenario())
