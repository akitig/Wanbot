import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from wanbot.config import parse_config
from wanbot.valorant.delivery import DiagnosisDelivery
from wanbot.valorant.questions import load_questions
from wanbot.valorant.scoring import DiagnosisScorer
from wanbot.valorant.service import DiagnosisService
from wanbot.valorant.session import SessionError


START = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def make_service(tmp_path):
    config = parse_config(
        {
            "DISCORD_TOKEN": "unit-test-token",
            "TEST_GUILD_ID": "200",
            "RUNTIME_DATA_DIR": str(tmp_path),
        }
    )
    return DiagnosisService(
        config,
        200,
        DiagnosisScorer(load_questions()),
    )


def make_member():
    return SimpleNamespace(
        id=10,
        guild=SimpleNamespace(id=200),
        bot=False,
        send=AsyncMock(),
    )


def make_delivery(service, available=None):
    return DiagnosisDelivery(
        200,
        service,
        on_answer=AsyncMock(),
        is_available=available if available is not None else Mock(return_value=True),
        timeout_seconds=1800,
        clock=lambda: START,
    )


def forbidden():
    return discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"),
        "PRIVATE_DETAIL",
    )


def stop_views(member):
    for call in member.send.await_args_list:
        view = call.kwargs.get("view")
        if view is not None:
            view.stop()


async def start(service):
    return await service.start(
        user_id=10,
        invoker_id=20,
        timeout_seconds=1800,
        now=START,
    )


def test_first_delivery_sends_introduction_and_question(tmp_path):
    async def scenario():
        service = make_service(tmp_path)
        member = make_member()
        try:
            await make_delivery(service).send(member, 20)

            assert member.send.await_count == 2
            assert "全15問です。" in member.send.await_args_list[0].args[0]
            assert "（1 / 15）" in member.send.await_args_list[1].args[0]

            progress = await service.resume(10, now=START)
            assert progress.session.invoker_id == 20
            assert progress.session.current_question.id == "q01"
        finally:
            stop_views(member)
            await service.close()

    asyncio.run(scenario())


def test_introduction_failure_does_not_create_session(tmp_path):
    async def scenario():
        service = make_service(tmp_path)
        member = make_member()
        member.send.side_effect = forbidden()
        try:
            with pytest.raises(discord.Forbidden):
                await make_delivery(service).send(member, 20)

            assert await service.resume(10, now=START) is None
        finally:
            await service.close()

    asyncio.run(scenario())


def test_question_forbidden_cancels_new_unanswered_session(tmp_path):
    async def scenario():
        service = make_service(tmp_path)
        member = make_member()
        member.send.side_effect = [None, forbidden()]
        try:
            with pytest.raises(discord.Forbidden):
                await make_delivery(service).send(member, 20)

            assert await service.resume(10, now=START) is None
            view = member.send.await_args_list[1].kwargs["view"]
            assert view.is_finished()
        finally:
            stop_views(member)
            await service.close()

    asyncio.run(scenario())


def test_uncertain_send_failure_keeps_session(tmp_path):
    async def scenario():
        service = make_service(tmp_path)
        member = make_member()
        error = discord.HTTPException(
            SimpleNamespace(status=500, reason="Server Error"),
            "PRIVATE_DETAIL",
        )
        member.send.side_effect = [None, error]
        try:
            with pytest.raises(discord.HTTPException):
                await make_delivery(service).send(member, 20)

            progress = await service.resume(10, now=START)
            assert progress is not None
            assert progress.session.current_question.id == "q01"
        finally:
            stop_views(member)
            await service.close()

    asyncio.run(scenario())


def test_existing_session_resends_current_question(tmp_path):
    async def scenario():
        service = make_service(tmp_path)
        member = make_member()
        try:
            initial = await start(service)
            await service.answer(
                user_id=10,
                session_id=initial.session.session_id,
                actor_id=10,
                question_id="q01",
                choice_id="a",
                now=START,
            )

            await make_delivery(service).send(member, 99)

            member.send.assert_awaited_once()
            assert "（2 / 15）" in member.send.call_args.args[0]
            progress = await service.resume(10, now=START)
            assert progress.session.session_id == initial.session.session_id
            assert progress.session.invoker_id == 20
            assert dict(progress.session.answers) == {"q01": "a"}
        finally:
            stop_views(member)
            await service.close()

    asyncio.run(scenario())


def test_resend_forbidden_does_not_delete_existing_session(tmp_path):
    async def scenario():
        service = make_service(tmp_path)
        member = make_member()
        member.send.side_effect = forbidden()
        try:
            initial = await start(service)

            with pytest.raises(discord.Forbidden):
                await make_delivery(service).send(member, 20)

            progress = await service.resume(10, now=START)
            assert progress.session.session_id == initial.session.session_id
        finally:
            stop_views(member)
            await service.close()

    asyncio.run(scenario())


def test_answered_session_cannot_be_cancelled(tmp_path):
    async def scenario():
        service = make_service(tmp_path)
        try:
            initial = await start(service)
            await service.answer(
                user_id=10,
                session_id=initial.session.session_id,
                actor_id=10,
                question_id="q01",
                choice_id="a",
                now=START,
            )

            with pytest.raises(SessionError):
                await service.cancel_unanswered(
                    user_id=10,
                    session_id=initial.session.session_id,
                    now=START,
                )

            progress = await service.resume(10, now=START)
            assert dict(progress.session.answers) == {"q01": "a"}
        finally:
            await service.close()

    asyncio.run(scenario())


def test_old_session_id_cannot_cancel_replacement(tmp_path):
    async def scenario():
        service = make_service(tmp_path)
        try:
            original = await start(service)
            later = START + timedelta(hours=1)
            replacement = await service.start(
                user_id=10,
                invoker_id=20,
                timeout_seconds=1800,
                now=later,
            )

            with pytest.raises(SessionError):
                await service.cancel_unanswered(
                    user_id=10,
                    session_id=original.session.session_id,
                    now=later,
                )

            progress = await service.resume(10, now=later)
            assert progress.session.session_id == replacement.session.session_id
        finally:
            await service.close()

    asyncio.run(scenario())


def test_disabled_feature_does_not_send_or_start(tmp_path):
    async def scenario():
        service = make_service(tmp_path)
        member = make_member()
        try:
            delivery = make_delivery(service, Mock(return_value=False))

            with pytest.raises(SessionError):
                await delivery.send(member, 20)

            member.send.assert_not_awaited()
            assert await service.resume(10, now=START) is None
        finally:
            await service.close()

    asyncio.run(scenario())
