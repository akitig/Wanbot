import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from wanbot.bump.discord_panel import (
    BumpPanelTargetError,
    DiscordBumpPanel,
)
from wanbot.bump.repository import BumpState


NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def not_found():
    response = SimpleNamespace(status=404, reason="Not Found")
    return discord.NotFound(
        response,
        {"code": 10008, "message": "Unknown Message"},
    )


def make_message(message_id):
    return SimpleNamespace(
        id=message_id,
        guild=SimpleNamespace(id=100),
        channel=SimpleNamespace(id=200),
        author=SimpleNamespace(id=300),
        webhook_id=None,
        edit=AsyncMock(),
        delete=AsyncMock(),
    )


def setup_panel():
    channel = Mock(spec=discord.TextChannel)
    channel.id = 200
    channel.guild = SimpleNamespace(id=100)
    channel.fetch_message = AsyncMock()
    channel.send = AsyncMock()

    client = Mock()
    client.user = SimpleNamespace(id=300)
    client.get_channel.return_value = channel

    repository = SimpleNamespace(
        set_panel_message_id=AsyncMock(),
        read=AsyncMock(),
    )

    panel = DiscordBumpPanel(
        client=client,
        guild_id=100,
        channel_id=200,
        repository=repository,
        on_guide=AsyncMock(),
    )

    return panel, channel, repository


def test_initial_render_creates_and_saves_panel():
    async def run():
        panel, channel, repository = setup_panel()
        channel.send.return_value = make_message(500)

        try:
            await panel.render(BumpState(), NOW, False)

            channel.fetch_message.assert_not_awaited()
            channel.send.assert_awaited_once()
            repository.set_panel_message_id.assert_awaited_once_with(500)

            mentions = channel.send.call_args.kwargs["allowed_mentions"]
            assert mentions.everyone is False
            assert mentions.roles is False
            assert mentions.users is False
        finally:
            await panel.close()

    asyncio.run(run())


def test_existing_panel_is_edited_without_reposting():
    async def run():
        panel, channel, repository = setup_panel()
        previous = make_message(400)
        channel.fetch_message.return_value = previous

        try:
            await panel.render(
                BumpState(panel_message_id=400),
                NOW,
                False,
            )

            channel.fetch_message.assert_awaited_once_with(400)
            previous.edit.assert_awaited_once()
            channel.send.assert_not_awaited()
            previous.delete.assert_not_awaited()
            repository.set_panel_message_id.assert_not_awaited()
        finally:
            await panel.close()

    asyncio.run(run())


def test_missing_panel_is_recreated():
    async def run():
        panel, channel, repository = setup_panel()
        channel.fetch_message.side_effect = not_found()
        channel.send.return_value = make_message(500)

        try:
            await panel.render(
                BumpState(panel_message_id=400),
                NOW,
                False,
            )

            channel.send.assert_awaited_once()
            repository.set_panel_message_id.assert_awaited_once_with(500)
        finally:
            await panel.close()

    asyncio.run(run())


def test_repost_saves_new_panel_before_deleting_old_panel():
    async def run():
        panel, channel, repository = setup_panel()
        previous = make_message(400)
        new_message = make_message(500)
        channel.fetch_message.return_value = previous
        events = []

        async def send(**kwargs):
            events.append("send")
            return new_message

        async def save(message_id):
            assert message_id == 500
            events.append("save")

        async def delete():
            events.append("delete")

        channel.send.side_effect = send
        repository.set_panel_message_id.side_effect = save
        previous.delete.side_effect = delete

        try:
            await panel.render(
                BumpState(panel_message_id=400),
                NOW,
                True,
            )

            assert events == ["send", "save", "delete"]
            previous.edit.assert_not_awaited()
        finally:
            await panel.close()

    asyncio.run(run())


def test_foreign_message_is_not_changed():
    async def run():
        panel, channel, repository = setup_panel()
        previous = make_message(400)
        previous.author.id = 999
        channel.fetch_message.return_value = previous

        try:
            with pytest.raises(BumpPanelTargetError):
                await panel.render(
                    BumpState(panel_message_id=400),
                    NOW,
                    True,
                )

            previous.edit.assert_not_awaited()
            previous.delete.assert_not_awaited()
            channel.send.assert_not_awaited()
            repository.set_panel_message_id.assert_not_awaited()
        finally:
            await panel.close()

    asyncio.run(run())


def test_wrong_guild_channel_is_rejected():
    async def run():
        panel, channel, repository = setup_panel()
        channel.guild.id = 999

        try:
            with pytest.raises(BumpPanelTargetError):
                await panel.render(BumpState(), NOW, False)

            channel.fetch_message.assert_not_awaited()
            channel.send.assert_not_awaited()
            repository.set_panel_message_id.assert_not_awaited()
        finally:
            await panel.close()

    asyncio.run(run())


def test_send_failure_preserves_old_panel():
    async def run():
        panel, channel, repository = setup_panel()
        previous = make_message(400)
        channel.fetch_message.return_value = previous
        channel.send.side_effect = RuntimeError("Send failed")

        try:
            with pytest.raises(RuntimeError):
                await panel.render(
                    BumpState(panel_message_id=400),
                    NOW,
                    True,
                )

            previous.delete.assert_not_awaited()
            repository.set_panel_message_id.assert_not_awaited()
        finally:
            await panel.close()

    asyncio.run(run())


def test_save_retry_does_not_send_another_panel():
    async def run():
        panel, channel, repository = setup_panel()
        previous = make_message(400)
        new_message = make_message(500)

        channel.fetch_message.side_effect = [previous, new_message]
        channel.send.return_value = new_message
        repository.set_panel_message_id.side_effect = [
            RuntimeError("Save failed"),
            None,
        ]
        repository.read.return_value = BumpState(panel_message_id=500)

        try:
            with pytest.raises(RuntimeError):
                await panel.render(
                    BumpState(panel_message_id=400),
                    NOW,
                    True,
                )

            previous.delete.assert_not_awaited()

            await panel.render(
                BumpState(panel_message_id=400),
                NOW,
                False,
            )

            channel.send.assert_awaited_once()
            assert repository.set_panel_message_id.await_count == 2
            previous.delete.assert_awaited_once()
            new_message.edit.assert_awaited_once()
        finally:
            await panel.close()

    asyncio.run(run())


def test_panel_deleted_during_edit_is_recreated():
    async def run():
        panel, channel, repository = setup_panel()
        previous = make_message(400)
        previous.edit.side_effect = not_found()
        channel.fetch_message.return_value = previous
        channel.send.return_value = make_message(500)

        try:
            await panel.render(
                BumpState(panel_message_id=400),
                NOW,
                False,
            )

            channel.send.assert_awaited_once()
            repository.set_panel_message_id.assert_awaited_once_with(500)
        finally:
            await panel.close()

    asyncio.run(run())


def test_closed_panel_does_not_post():
    async def run():
        panel, channel, repository = setup_panel()
        await panel.close()
        await panel.close()

        with pytest.raises(RuntimeError):
            await panel.render(BumpState(), NOW, False)

        channel.send.assert_not_awaited()
        repository.set_panel_message_id.assert_not_awaited()

    asyncio.run(run())
