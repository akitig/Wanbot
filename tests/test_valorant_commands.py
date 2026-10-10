import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
from discord import app_commands

from wanbot.config import parse_config
from wanbot.permissions import AccessDenied
from wanbot.storage.json_store import StorageError
from wanbot.valorant.commands import register_diagnosis_command
from wanbot.valorant.session import SessionError


def make_member(user_id=10, guild_id=200, roles=(), bot=False):
    member = Mock(spec=discord.Member)
    member.id = user_id
    member.guild = SimpleNamespace(id=guild_id)
    member.roles = [SimpleNamespace(id=role_id) for role_id in roles]
    member.bot = bot
    return member


def make_interaction():
    return SimpleNamespace(
        guild_id=200,
        user=make_member(user_id=20, roles=(2000,)),
        response=SimpleNamespace(
            send_message=AsyncMock(),
            defer=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
    )


@asynccontextmanager
async def command_case(tmp_path, enabled=True):
    config = parse_config(
        {
            "DISCORD_TOKEN": "unit-test-token",
            "PROD_GUILD_ID": "100",
            "TEST_GUILD_ID": "200",
            "TEST_ADMIN_ROLE_IDS": "2000",
            "TEST_ENABLED_FEATURES": "valorant_diagnosis" if enabled else "",
            "RUNTIME_DATA_DIR": str(tmp_path),
        }
    )
    client = discord.Client(intents=discord.Intents.none())
    tree = app_commands.CommandTree(client)
    sender = AsyncMock()
    available = Mock(return_value=True)

    try:
        yield SimpleNamespace(
            config=config,
            tree=tree,
            sender=sender,
            available=available,
        )
    finally:
        await client.close()


def register(case):
    return register_diagnosis_command(
        case.tree,
        case.config,
        200,
        is_available=case.available,
        send_diagnosis=case.sender,
    )


def test_command_is_registered_only_in_requested_guild(tmp_path):
    async def scenario():
        async with command_case(tmp_path) as case:
            command = register(case)

            assert command.name == "valo_role"
            assert command.guild_only
            assert case.tree.get_command(
                "valo_role",
                guild=discord.Object(id=200),
            ) is command
            assert case.tree.get_command(
                "valo_role",
                guild=discord.Object(id=100),
            ) is None
            assert case.tree.get_command("valo_role") is None

    asyncio.run(scenario())


def test_disabled_feature_cannot_register_command(tmp_path):
    async def scenario():
        async with command_case(tmp_path, enabled=False) as case:
            with pytest.raises(AccessDenied):
                register(case)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "case_name",
    ["non_manager", "other_guild", "dm", "not_member"],
)
def test_unauthorized_invocation_does_not_send_dm(tmp_path, case_name):
    async def scenario():
        async with command_case(tmp_path) as case:
            command = register(case)
            interaction = make_interaction()

            if case_name == "non_manager":
                interaction.user = make_member(user_id=20)
            elif case_name == "other_guild":
                interaction.guild_id = 999
            elif case_name == "dm":
                interaction.guild_id = None
            else:
                interaction.user = SimpleNamespace(id=20)

            await command.callback(interaction, make_member())

            case.sender.assert_not_awaited()
            interaction.response.send_message.assert_awaited_once()
            assert interaction.response.send_message.call_args.kwargs["ephemeral"]

    asyncio.run(scenario())


@pytest.mark.parametrize("case_name", ["bot", "other_guild"])
def test_invalid_target_does_not_receive_dm(tmp_path, case_name):
    async def scenario():
        async with command_case(tmp_path) as case:
            command = register(case)
            member = make_member(
                bot=case_name == "bot",
                guild_id=999 if case_name == "other_guild" else 200,
            )

            await command.callback(make_interaction(), member)

            case.sender.assert_not_awaited()

    asyncio.run(scenario())


def test_stopped_feature_does_not_send_dm(tmp_path):
    async def scenario():
        async with command_case(tmp_path) as case:
            case.available.return_value = False
            command = register(case)
            interaction = make_interaction()

            await command.callback(interaction, make_member())

            case.sender.assert_not_awaited()
            interaction.response.defer.assert_not_awaited()

    asyncio.run(scenario())


def test_manager_can_send_diagnosis(tmp_path):
    async def scenario():
        async with command_case(tmp_path) as case:
            command = register(case)
            interaction = make_interaction()
            member = make_member()

            await command.callback(interaction, member)

            interaction.response.defer.assert_awaited_once_with(
                ephemeral=True,
                thinking=True,
            )
            case.sender.assert_awaited_once_with(member, 20)
            interaction.followup.send.assert_awaited_once()
            call = interaction.followup.send.call_args
            assert call.args[0] == "診断DMを送信しました。"
            assert call.kwargs["ephemeral"]

    asyncio.run(scenario())


@pytest.mark.parametrize("error_kind", ["session", "storage", "forbidden"])
def test_expected_failure_does_not_report_success(
    tmp_path,
    monkeypatch,
    error_kind,
):
    monkeypatch.setattr("wanbot.valorant.commands.log_event", Mock())

    async def scenario():
        async with command_case(tmp_path) as case:
            if error_kind == "session":
                error = SessionError("PRIVATE_DETAIL")
            elif error_kind == "storage":
                error = StorageError("PRIVATE_DETAIL")
            else:
                error = discord.Forbidden(
                    SimpleNamespace(status=403, reason="Forbidden"),
                    "PRIVATE_DETAIL",
                )

            case.sender.side_effect = error
            command = register(case)
            interaction = make_interaction()

            await command.callback(interaction, make_member())

            message = interaction.followup.send.call_args.args[0]
            assert message != "診断DMを送信しました。"
            assert "PRIVATE_DETAIL" not in message

    asyncio.run(scenario())
