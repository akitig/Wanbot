import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
import pytest
from discord import app_commands

from wanbot.config import parse_config
from wanbot.storage.json_store import StorageError
from wanbot.valorant.commands import register_diagnosis_command
from wanbot.valorant.delivery import DiagnosisDelivery
from wanbot.valorant.feature import ValorantDiagnosisFeature
from wanbot.valorant.presentation import format_result
from wanbot.valorant.questions import load_questions
from wanbot.valorant.scoring import ClassificationPolicy, DiagnosisScorer
from wanbot.valorant.service import DiagnosisService
from wanbot.valorant.session import SessionError


START = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def config_for(tmp_path):
    return parse_config({
        "DISCORD_TOKEN": "unit-test-token",
        "TEST_GUILD_ID": "200",
        "TEST_ADMIN_ROLE_IDS": "201",
        "TEST_ENABLED_FEATURES": "valorant_diagnosis",
        "RUNTIME_DATA_DIR": str(tmp_path),
    })


def service_for(config, policy=None):
    return DiagnosisService(
        config,
        200,
        DiagnosisScorer(load_questions(), policy=policy),
    )


def member_for(user_id=10, roles=()):
    member = Mock(spec=discord.Member)
    member.id = user_id
    member.guild = SimpleNamespace(id=200)
    member.bot = False
    member.roles = [SimpleNamespace(id=value) for value in roles]
    member.send = AsyncMock()
    return member


def delivery_for(service):
    return DiagnosisDelivery(
        200,
        service,
        on_answer=AsyncMock(),
        is_available=lambda: True,
        timeout_seconds=1800,
        clock=lambda: START + timedelta(minutes=1),
    )


def interaction_for(user_id=10, roles=(), guild_id=None):
    response = SimpleNamespace(
        is_done=Mock(return_value=False),
        send_message=AsyncMock(),
    )

    async def defer(**kwargs):
        response.is_done.return_value = True

    response.defer = AsyncMock(side_effect=defer)
    return SimpleNamespace(
        user=member_for(user_id, roles),
        guild_id=guild_id,
        response=response,
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


async def begin(service):
    return await service.start(
        user_id=10,
        invoker_id=20,
        timeout_seconds=1800,
        now=START,
    )


async def answer(service, session, number):
    return await service.answer(
        user_id=10,
        session_id=session.session_id,
        actor_id=10,
        question_id=f"q{number:02d}",
        choice_id="a",
        now=START + timedelta(seconds=number),
    )


def stop_sent_views(member):
    for call in member.send.await_args_list:
        view = call.kwargs.get("view")
        if view is not None:
            view.stop()


def test_restored_button_can_continue_saved_diagnosis(tmp_path):
    async def scenario():
        config = config_for(tmp_path)
        first = service_for(config)
        try:
            initial = await begin(first)
            await answer(first, initial.session, 1)
        finally:
            await first.close()

        client = discord.Client(intents=discord.Intents.none())
        client.tree = app_commands.CommandTree(client)
        client.feature_available = Mock(return_value=True)
        feature = ValorantDiagnosisFeature(
            client,
            config,
            config.guild_for(200),
            clock=lambda: START + timedelta(minutes=1),
        )
        interaction = interaction_for()
        try:
            await feature.start()
            restored_views = list(client.persistent_views)
            assert len(restored_views) == 1

            view = restored_views[0]
            assert ":q02:a" in view.children[0].custom_id
            await view.children[0].callback(interaction)

            updated = await feature._service.resume(
                10, now=START + timedelta(minutes=1)
            )
            assert updated.session.current_question.id == "q03"
            assert updated.session.session_id == initial.session.session_id
            assert "（3 / 15）" in (
                interaction.edit_original_response.call_args.kwargs["content"]
            )
        finally:
            if interaction.edit_original_response.await_count:
                new_view = interaction.edit_original_response.call_args.kwargs["view"]
                if new_view is not None:
                    new_view.stop()
            await feature.stop()
            assert not client.persistent_views
            await client.close()

    asyncio.run(scenario())


def test_restart_does_not_restore_expired_buttons(tmp_path):
    async def scenario():
        config = config_for(tmp_path)
        first = service_for(config)
        try:
            await begin(first)
        finally:
            await first.close()

        client = discord.Client(intents=discord.Intents.none())
        client.tree = app_commands.CommandTree(client)
        client.feature_available = Mock(return_value=True)
        feature = ValorantDiagnosisFeature(
            client,
            config,
            config.guild_for(200),
            clock=lambda: START + timedelta(hours=1),
        )
        try:
            await feature.start()
            assert not client.persistent_views
        finally:
            await feature.stop()
            await client.close()

    asyncio.run(scenario())


def test_saved_result_can_be_resent_after_restart_and_send_failure(tmp_path):
    async def scenario():
        config = config_for(tmp_path)
        first = service_for(config)
        try:
            progress = await begin(first)
            for number in range(1, 16):
                progress = await answer(first, progress.session, number)
            expected = format_result(progress.result)
        finally:
            await first.close()

        # 現在の設定を変えても、保存時の判定を再表示する。
        second = service_for(
            config,
            ClassificationPolicy(gachi_minimum=0.99, neutral_minimum=0.98),
        )
        member = member_for()
        member.send.side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"),
            "test",
        )
        try:
            delivery = delivery_for(second)
            with pytest.raises(discord.Forbidden):
                await delivery.resend(member, 20)

            member.send.side_effect = None
            await delivery.resend(member, 20)

            assert expected in member.send.call_args.args[0]
            assert "view" not in member.send.call_args.kwargs
            assert await second.list_sessions(now=START) == []
        finally:
            await second.close()

    asyncio.run(scenario())


def test_pending_result_is_recovered_before_resending(tmp_path):
    async def scenario():
        service = service_for(config_for(tmp_path))
        member = member_for()
        try:
            progress = await begin(service)
            for number in range(1, 15):
                progress = await answer(service, progress.session, number)

            with patch.object(
                service._results,
                "save_completed",
                AsyncMock(side_effect=StorageError("test")),
            ):
                with pytest.raises(StorageError):
                    await answer(service, progress.session, 15)

            await delivery_for(service).resend(member, 20)

            assert "フィードバック傾向" in member.send.call_args.args[0]
            assert await service.latest_result(10) is not None
            assert await service.list_sessions(now=START) == []
        finally:
            await service.close()

    asyncio.run(scenario())


def test_resend_keeps_existing_answers_and_deadline(tmp_path):
    async def scenario():
        service = service_for(config_for(tmp_path))
        member = member_for()
        try:
            initial = await begin(service)
            await answer(service, initial.session, 1)

            await delivery_for(service).resend(member, 99)

            current = await service.resume(
                10, now=START + timedelta(minutes=1)
            )
            assert current.session.session_id == initial.session.session_id
            assert current.session.expires_at == initial.session.expires_at
            assert dict(current.session.answers) == {"q01": "a"}
            assert "（2 / 15）" in member.send.call_args.args[0]
        finally:
            stop_sent_views(member)
            await service.close()

    asyncio.run(scenario())


def test_resend_without_data_does_not_start_diagnosis(tmp_path):
    async def scenario():
        service = service_for(config_for(tmp_path))
        member = member_for()
        try:
            with pytest.raises(SessionError):
                await delivery_for(service).resend(member, 20)

            member.send.assert_not_awaited()
            assert await service.list_sessions(now=START) == []
        finally:
            await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("is_manager", [True, False])
def test_resend_option_uses_same_manager_check(tmp_path, is_manager):
    async def scenario():
        config = config_for(tmp_path)
        client = discord.Client(intents=discord.Intents.none())
        tree = app_commands.CommandTree(client)
        sender = AsyncMock()
        resender = AsyncMock()
        try:
            command = register_diagnosis_command(
                tree,
                config,
                200,
                is_available=lambda: True,
                send_diagnosis=sender,
                resend_diagnosis=resender,
            )
            interaction = interaction_for(
                user_id=20,
                roles=(201,) if is_manager else (),
                guild_id=200,
            )
            target = member_for()
            await command.callback(interaction, target, resend=True)

            sender.assert_not_awaited()
            if is_manager:
                resender.assert_awaited_once_with(target, 20)
            else:
                resender.assert_not_awaited()
        finally:
            await client.close()

    asyncio.run(scenario())
