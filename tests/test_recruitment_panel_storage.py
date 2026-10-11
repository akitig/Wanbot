import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from wanbot.config import parse_config
from wanbot.recruitment.discord_panel import (
    DiscordRecruitmentPanel,
    RecruitmentPanelTargetError,
)
from wanbot.recruitment.repository import (
    RecruitmentPanelDataError,
    RecruitmentPanelRepository,
)


def make_config(tmp_path):
    return parse_config({
        "DISCORD_TOKEN": "test-token",
        "PROD_GUILD_ID": "101",
        "TEST_GUILD_ID": "100",
        "RUNTIME_DATA_DIR": str(tmp_path / "runtime"),
    })


def test_panel_id_survives_repository_reopen(tmp_path):
    async def run():
        config = make_config(tmp_path)
        repository = RecruitmentPanelRepository(config, 100)
        try:
            assert await repository.read() is None
            await repository.set_panel_message_id(500)
        finally:
            await repository.close()

        reopened = RecruitmentPanelRepository(config, 100)
        try:
            assert await reopened.read() == 500
        finally:
            await reopened.close()

    asyncio.run(run())


def test_panel_data_is_separated_by_guild(tmp_path):
    async def run():
        config = make_config(tmp_path)
        test = RecruitmentPanelRepository(config, 100)
        prod = RecruitmentPanelRepository(config, 101)

        try:
            await test.set_panel_message_id(500)
            assert await test.read() == 500
            assert await prod.read() is None
        finally:
            await test.close()
            await prod.close()

    asyncio.run(run())


@pytest.mark.parametrize("message_id", [0, -1, True, 1 << 64])
def test_invalid_panel_id_does_not_replace_saved_id(
    tmp_path,
    message_id,
):
    async def run():
        repository = RecruitmentPanelRepository(
            make_config(tmp_path),
            100,
        )
        try:
            await repository.set_panel_message_id(500)

            with pytest.raises(ValueError):
                await repository.set_panel_message_id(message_id)

            assert await repository.read() == 500
        finally:
            await repository.close()

    asyncio.run(run())


def test_malformed_saved_data_is_rejected(tmp_path):
    async def run():
        repository = RecruitmentPanelRepository(
            make_config(tmp_path),
            100,
        )
        try:
            def corrupt(data):
                return {
                    "state_version": 1,
                    "panel_message_id": "not-an-id",
                }

            await repository._store.update(corrupt)

            with pytest.raises(RecruitmentPanelDataError):
                await repository.read()

            with pytest.raises(RecruitmentPanelDataError):
                await repository.set_panel_message_id(500)
        finally:
            await repository.close()

    asyncio.run(run())


def make_message(message_id):
    return SimpleNamespace(
        id=message_id,
        guild=SimpleNamespace(id=100),
        channel=SimpleNamespace(id=200),
        author=SimpleNamespace(id=300),
        webhook_id=None,
        edit=AsyncMock(),
    )


def setup_panel():
    channel = Mock(spec=discord.TextChannel)
    channel.id = 200
    channel.guild = SimpleNamespace(id=100)
    channel.fetch_message = AsyncMock()
    channel.send = AsyncMock(return_value=make_message(500))

    client = Mock(spec=discord.Client)
    client.user = SimpleNamespace(id=300)
    client.get_channel.return_value = channel

    repository = SimpleNamespace(
        read=AsyncMock(return_value=None),
        set_panel_message_id=AsyncMock(),
    )

    panel = DiscordRecruitmentPanel(
        client=client,
        guild_id=100,
        channel_id=200,
        repository=repository,
        on_begin=AsyncMock(),
    )

    return panel, channel, repository


def test_initial_panel_is_created_and_saved():
    async def run():
        panel, channel, repository = setup_panel()
        try:
            await panel.ensure()

            channel.send.assert_awaited_once()
            repository.set_panel_message_id.assert_awaited_once_with(500)

            kwargs = channel.send.call_args.kwargs
            assert kwargs["view"].is_persistent()
            assert kwargs["allowed_mentions"].everyone is False
            assert kwargs["allowed_mentions"].roles is False
        finally:
            await panel.close()

    asyncio.run(run())


def test_saved_panel_is_updated_without_new_post():
    async def run():
        panel, channel, repository = setup_panel()
        repository.read.return_value = 400
        message = make_message(400)
        channel.fetch_message.return_value = message

        try:
            await panel.ensure()

            channel.fetch_message.assert_awaited_once_with(400)
            message.edit.assert_awaited_once()
            channel.send.assert_not_awaited()
        finally:
            await panel.close()

    asyncio.run(run())


def test_deleted_panel_is_recreated():
    async def run():
        panel, channel, repository = setup_panel()
        repository.read.return_value = 400
        response = SimpleNamespace(status=404, reason="Not Found")
        channel.fetch_message.side_effect = discord.NotFound(
            response,
            {"code": 10008, "message": "Unknown Message"},
        )

        try:
            await panel.ensure()

            channel.send.assert_awaited_once()
            repository.set_panel_message_id.assert_awaited_once_with(500)
        finally:
            await panel.close()

    asyncio.run(run())


def test_foreign_message_is_not_edited():
    async def run():
        panel, channel, repository = setup_panel()
        repository.read.return_value = 400
        message = make_message(400)
        message.author.id = 999
        channel.fetch_message.return_value = message

        try:
            with pytest.raises(RecruitmentPanelTargetError):
                await panel.ensure()

            message.edit.assert_not_awaited()
            channel.send.assert_not_awaited()
        finally:
            await panel.close()

    asyncio.run(run())


def test_wrong_guild_channel_is_rejected():
    async def run():
        panel, channel, repository = setup_panel()
        channel.guild.id = 999

        try:
            with pytest.raises(RecruitmentPanelTargetError):
                await panel.ensure()

            channel.send.assert_not_awaited()
            repository.read.assert_not_awaited()
        finally:
            await panel.close()

    asyncio.run(run())


def test_save_retry_reuses_created_panel():
    async def run():
        panel, channel, repository = setup_panel()
        message = make_message(500)
        channel.send.return_value = message
        channel.fetch_message.return_value = message
        repository.set_panel_message_id.side_effect = [
            RuntimeError("Save failed"),
            None,
        ]
        repository.read.side_effect = [None, 500]

        try:
            with pytest.raises(RuntimeError, match="Save failed"):
                await panel.ensure()

            await panel.ensure()

            channel.send.assert_awaited_once()
            assert repository.set_panel_message_id.await_count == 2
            message.edit.assert_awaited_once()
        finally:
            await panel.close()

    asyncio.run(run())


def test_closed_panel_does_not_post():
    async def run():
        panel, channel, repository = setup_panel()
        await panel.close()
        await panel.close()

        with pytest.raises(RuntimeError):
            await panel.ensure()

        channel.send.assert_not_awaited()

    asyncio.run(run())
