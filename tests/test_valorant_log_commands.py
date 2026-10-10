import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
from discord import app_commands

from wanbot.config import parse_config
from wanbot.valorant.log_commands import register_log_command
from wanbot.valorant.questions import load_questions


def member(user_id=20, roles=(201,)):
    value = Mock(spec=discord.Member)
    value.id = user_id
    value.guild = SimpleNamespace(id=200)
    value.roles = [SimpleNamespace(id=role) for role in roles]
    return value


def interaction(user_id=20, roles=(201,), guild_id=200):
    response = SimpleNamespace(
        is_done=Mock(return_value=False),
        send_message=AsyncMock(),
    )

    async def defer(**kwargs):
        response.is_done.return_value = True

    response.defer = AsyncMock(side_effect=defer)
    return SimpleNamespace(
        user=member(user_id, roles),
        guild_id=guild_id,
        response=response,
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


@asynccontextmanager
async def case(tmp_path, monkeypatch):
    config = parse_config({
        "DISCORD_TOKEN": "unit-test-token",
        "TEST_GUILD_ID": "200",
        "TEST_ADMIN_ROLE_IDS": "201",
        "TEST_ENABLED_FEATURES": "valorant_diagnosis",
        "RUNTIME_DATA_DIR": str(tmp_path),
    })
    # 表示内容自体は前段のテストで確認済み。
    monkeypatch.setattr(
        "wanbot.valorant.log_commands.format_log_pages",
        Mock(return_value=("概要ページ", "回答ページ")),
    )
    client = discord.Client(intents=discord.Intents.none())
    tree = app_commands.CommandTree(client)
    loader = AsyncMock(return_value={"saved": True})
    available = Mock(return_value=True)
    command = register_log_command(
        tree,
        config,
        200,
        question_set=load_questions(),
        is_available=available,
        load_record=loader,
    )
    initial = interaction()
    try:
        yield SimpleNamespace(
            command=command,
            loader=loader,
            available=available,
            interaction=initial,
        )
    finally:
        for call in initial.edit_original_response.await_args_list:
            view = call.kwargs.get("view")
            if view is not None:
                view.stop()
        await client.close()


@pytest.mark.parametrize("lookup", ["member", "user_id"])
def test_manager_can_read_latest_record(tmp_path, monkeypatch, lookup):
    async def scenario():
        async with case(tmp_path, monkeypatch) as c:
            kwargs = (
                {"member": member(10)}
                if lookup == "member"
                else {"user_id": "10"}
            )
            await c.command.callback(c.interaction, **kwargs)

            c.loader.assert_awaited_once_with(10)
            c.interaction.response.defer.assert_awaited_once_with(
                ephemeral=True, thinking=True
            )
            options = c.interaction.edit_original_response.call_args.kwargs
            assert options["content"] == "概要ページ"
            assert options["allowed_mentions"].to_dict() == {"parse": []}

    asyncio.run(scenario())


@pytest.mark.parametrize("reason", ["roles", "guild", "dm", "disabled"])
def test_unauthorized_request_does_not_read_data(tmp_path, monkeypatch, reason):
    async def scenario():
        async with case(tmp_path, monkeypatch) as c:
            if reason == "roles":
                c.interaction.user.roles = []
            elif reason == "guild":
                c.interaction.guild_id = 999
            elif reason == "dm":
                c.interaction.guild_id = None
            else:
                c.available.return_value = False

            await c.command.callback(c.interaction, user_id="10")

            c.loader.assert_not_awaited()
            c.interaction.response.send_message.assert_awaited_once()
            assert c.interaction.response.send_message.call_args.kwargs["ephemeral"]

    asyncio.run(scenario())


@pytest.mark.parametrize("reason", ["neither", "both", "invalid_id"])
def test_invalid_target_does_not_read_data(tmp_path, monkeypatch, reason):
    async def scenario():
        async with case(tmp_path, monkeypatch) as c:
            if reason == "neither":
                kwargs = {}
            elif reason == "both":
                kwargs = {"member": member(10), "user_id": "10"}
            else:
                kwargs = {"user_id": "not-an-id"}

            await c.command.callback(c.interaction, **kwargs)

            c.loader.assert_not_awaited()

    asyncio.run(scenario())


def test_missing_record_has_no_buttons(tmp_path, monkeypatch):
    async def scenario():
        async with case(tmp_path, monkeypatch) as c:
            c.loader.return_value = None
            await c.command.callback(c.interaction, user_id="10")

            options = c.interaction.edit_original_response.call_args.kwargs
            assert "保存済み診断がありません" in options["content"]
            assert options["view"] is None

    asyncio.run(scenario())


def test_page_navigation_updates_content_and_buttons(tmp_path, monkeypatch):
    async def scenario():
        async with case(tmp_path, monkeypatch) as c:
            await c.command.callback(c.interaction, user_id="10")
            view = c.interaction.edit_original_response.call_args.kwargs["view"]

            assert view.previous.disabled
            assert not view.next_page.disabled

            next_click = interaction()
            await view.next_page.callback(next_click)

            assert next_click.edit_original_response.call_args.kwargs[
                "content"
            ] == "回答ページ"
            assert not view.previous.disabled
            assert view.next_page.disabled

            previous_click = interaction()
            await view.previous.callback(previous_click)

            assert previous_click.edit_original_response.call_args.kwargs[
                "content"
            ] == "概要ページ"

    asyncio.run(scenario())


@pytest.mark.parametrize("reason", ["other_owner", "role_removed", "disabled"])
def test_page_navigation_rechecks_access(tmp_path, monkeypatch, reason):
    async def scenario():
        async with case(tmp_path, monkeypatch) as c:
            await c.command.callback(c.interaction, user_id="10")
            view = c.interaction.edit_original_response.call_args.kwargs["view"]
            click = interaction()

            if reason == "other_owner":
                click.user.id = 99
            elif reason == "role_removed":
                click.user.roles = []
            else:
                c.available.return_value = False

            await view.next_page.callback(click)

            click.edit_original_response.assert_not_awaited()
            click.response.send_message.assert_awaited_once()

    asyncio.run(scenario())
