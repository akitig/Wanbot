import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from wanbot.valorant.question_view import AnswerSelection
from wanbot.valorant.questions import load_questions
from wanbot.valorant.scoring import DiagnosisScorer
from wanbot.valorant.service import DiagnosisProgress
from wanbot.valorant.session import DiagnosisSession, SessionError
from wanbot.welcome.questionnaire_forms import QuestionnaireContext
from wanbot.welcome.valorant_panel import WelcomeDiagnosisPanel


NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)


def make_case():
    welcome_id = "a" * 32
    context = QuestionnaireContext(
        guild_id=100,
        channel_id=500,
        user_id=300,
        session_id=welcome_id,
        is_available=lambda: True,
    )
    session = DiagnosisSession(
        guild_id=100,
        user_id=300,
        invoker_id=300,
        question_set=load_questions(),
        started_at=NOW,
        timeout_seconds=3600,
    )
    service = SimpleNamespace(
        resume=AsyncMock(return_value=DiagnosisProgress(session)),
        start=AsyncMock(),
        answer=AsyncMock(return_value=DiagnosisProgress(session)),
    )
    welcome = SimpleNamespace(
        get_active=AsyncMock(
            return_value=SimpleNamespace(
                state="active",
                session_id=welcome_id,
                user_id=300,
                channel_id=500,
            )
        )
    )
    questions = SimpleNamespace(
        get=AsyncMock(
            return_value=SimpleNamespace(
                session_id=welcome_id,
                user_id=300,
                progress=SimpleNamespace(
                    status="completed",
                    plays_valo=True,
                ),
            )
        )
    )
    stored = {}

    async def get(key):
        return stored.get(key)

    async def save(key, value):
        stored[key] = value

    repository = SimpleNamespace(
        get=AsyncMock(side_effect=get),
        save=AsyncMock(side_effect=save),
    )
    channel = Mock(spec=discord.TextChannel)
    channel.id = 500
    channel.guild = SimpleNamespace(id=100)
    channel.topic = f"wanbot:welcome:100:{welcome_id}"
    message = SimpleNamespace(
        id=700,
        author=SimpleNamespace(id=900),
        channel=channel,
        webhook_id=None,
        embeds=[],
        edit=AsyncMock(),
    )

    async def send(**kwargs):
        message.embeds = [kwargs["embed"]]
        return message

    async def history(*, limit):
        for item in ():
            yield item

    channel.send = AsyncMock(side_effect=send)
    channel.fetch_message = AsyncMock(return_value=message)
    channel.history = history

    client = SimpleNamespace(
        user=SimpleNamespace(id=900),
        fetch_channel=AsyncMock(return_value=channel),
    )
    panel = WelcomeDiagnosisPanel(
        client=client,
        context=context,
        welcome_repository=welcome,
        questionnaire_repository=questions,
        panel_repository=repository,
        service=service,
        lock=asyncio.Lock(),
        timeout_seconds=3600,
        clock=lambda: NOW,
    )
    interaction = SimpleNamespace(
        guild_id=100,
        channel_id=500,
        user=SimpleNamespace(id=300),
        message=message,
        response=SimpleNamespace(
            is_done=lambda: False,
            defer=AsyncMock(),
            send_message=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    selection = AnswerSelection(
        guild_id=100,
        user_id=300,
        session_id=session.session_id,
        question_id=session.current_question.id,
        choice_id=session.current_question.choices[0].id,
    )
    return SimpleNamespace(
        panel=panel,
        service=service,
        welcome=welcome,
        questions=questions,
        channel=channel,
        message=message,
        interaction=interaction,
        selection=selection,
        session=session,
    )


def test_reopening_updates_same_message():
    async def scenario():
        case = make_case()
        try:
            assert await case.panel.begin() == 700
            assert await case.panel.begin() == 700
            case.channel.send.assert_awaited_once()
            case.message.edit.assert_awaited_once()
            case.service.start.assert_not_awaited()
        finally:
            case.panel.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("reason", ["left", "incomplete", "no_valorant"])
def test_ineligible_welcome_cannot_start(reason):
    async def scenario():
        case = make_case()
        if reason == "left":
            case.welcome.get_active.return_value = None
        elif reason == "incomplete":
            case.questions.get.return_value.progress.status = "answering"
        else:
            case.questions.get.return_value.progress.plays_valo = False

        try:
            with pytest.raises(SessionError):
                await case.panel.begin()
            case.service.resume.assert_not_awaited()
            case.channel.send.assert_not_awaited()
        finally:
            case.panel.close()

    asyncio.run(scenario())


def test_another_user_cannot_answer():
    async def scenario():
        case = make_case()
        case.interaction.user.id = 301
        try:
            await case.panel.on_answer(case.interaction, case.selection)
            case.service.answer.assert_not_awaited()
        finally:
            case.panel.close()

    asyncio.run(scenario())


def test_answer_from_another_message_is_rejected():
    async def scenario():
        case = make_case()
        try:
            await case.panel.begin()
            case.interaction.message = SimpleNamespace(id=701)
            await case.panel.on_answer(case.interaction, case.selection)
            case.service.answer.assert_not_awaited()
            case.interaction.response.send_message.assert_awaited_once()
            options = (
                case.interaction.response.send_message.call_args.kwargs
            )
            assert options["ephemeral"] is True
        finally:
            case.panel.close()

    asyncio.run(scenario())


def test_answer_updates_existing_post():
    async def scenario():
        case = make_case()
        try:
            await case.panel.begin()
            await case.panel.on_answer(case.interaction, case.selection)
            case.service.answer.assert_awaited_once()
            case.channel.send.assert_awaited_once()
            case.message.edit.assert_awaited_once()
        finally:
            case.panel.close()

    asyncio.run(scenario())


def test_completed_diagnosis_displays_result_without_buttons():
    async def scenario():
        case = make_case()
        session = case.session
        while session.current_question is not None:
            question = session.current_question
            session.submit(
                actor_id=300,
                question_id=question.id,
                choice_id=question.choices[0].id,
                now=NOW,
            )

        result = DiagnosisScorer(session.question_set).evaluate(
            session.answers
        )
        case.service.resume.return_value = DiagnosisProgress(
            session=session,
            result=result,
        )
        try:
            await case.panel.begin()
            options = case.channel.send.call_args.kwargs
            assert options["view"] is None
            assert options["content"]
            case.service.start.assert_not_awaited()
        finally:
            case.panel.close()

    asyncio.run(scenario())



@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("回答期限を過ぎています。", "回答期限を過ぎています"),
        ("この診断は回答済みです。", "回答済みです"),
        ("現在の質問に回答してください。", "表示が古くなっています"),
        ("古い診断のボタンは使用できません。", "現在の診断では使用できません"),
    ],
)
def test_rejected_answer_shows_private_notice(reason, expected):
    async def scenario():
        case = make_case()
        try:
            await case.panel.begin()
            case.service.answer.side_effect = SessionError(reason)

            # defer後のDiscordと同じように、応答済みの状態にする。
            async def defer(**kwargs):
                case.interaction.response.is_done = lambda: True

            case.interaction.response.defer.side_effect = defer

            await case.panel.on_answer(case.interaction, case.selection)

            case.interaction.followup.send.assert_awaited_once()
            call = case.interaction.followup.send.call_args
            assert expected in call.args[0]
            assert call.kwargs["ephemeral"] is True

            # 拒否された回答では、質問・結果の投稿を更新しない。
            case.message.edit.assert_not_awaited()
            case.channel.send.assert_awaited_once()
        finally:
            case.panel.close()

    asyncio.run(scenario())


def test_unexpected_failure_is_not_swallowed():
    async def scenario():
        case = make_case()
        try:
            await case.panel.begin()
            case.service.answer.side_effect = RuntimeError("storage failure")
            with pytest.raises(RuntimeError):
                await case.panel.on_answer(case.interaction, case.selection)
        finally:
            case.panel.close()

    asyncio.run(scenario())
