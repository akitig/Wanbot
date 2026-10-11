import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from wanbot.recruitment.discord_panel import (
    DiscordRecruitmentPanel,
    RecruitmentPanelTargetError,
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
    previous = make_message(400)
    new = make_message(500)

    channel = Mock(spec=discord.TextChannel)
    channel.id = 200
    channel.guild = SimpleNamespace(id=100)
    channel.fetch_message = AsyncMock(return_value=previous)
    channel.send = AsyncMock(return_value=new)

    client = Mock(spec=discord.Client)
    client.user = SimpleNamespace(id=300)
    client.get_channel.return_value = channel

    repository = SimpleNamespace(
        read=AsyncMock(return_value=400),
        set_panel_message_id=AsyncMock(),
    )

    panel = DiscordRecruitmentPanel(
        client=client,
        guild_id=100,
        channel_id=200,
        repository=repository,
        on_begin=AsyncMock(),
    )

    return SimpleNamespace(
        panel=panel,
        channel=channel,
        repository=repository,
        previous=previous,
        new=new,
    )


def test_move_posts_and_saves_before_deleting_old_panel():
    async def run():
        item = setup_panel()
        events = []

        async def send(**kwargs):
            events.append("send")
            return item.new

        async def save(message_id):
            assert message_id == 500
            events.append("save")

        async def delete():
            events.append("delete")

        item.channel.send.side_effect = send
        item.repository.set_panel_message_id.side_effect = save
        item.previous.delete.side_effect = delete

        try:
            await item.panel.move_to_bottom()

            assert events == ["send", "save", "delete"]
            item.previous.edit.assert_not_awaited()

            kwargs = item.channel.send.call_args.kwargs
            assert kwargs["silent"] is True
            assert kwargs["allowed_mentions"].everyone is False
            assert kwargs["allowed_mentions"].roles is False
            assert kwargs["allowed_mentions"].users is False
        finally:
            await item.panel.close()

    asyncio.run(run())


def test_failed_send_keeps_old_panel():
    async def run():
        item = setup_panel()
        item.channel.send.side_effect = RuntimeError("Send failed")

        try:
            with pytest.raises(RuntimeError, match="Send failed"):
                await item.panel.move_to_bottom()

            item.previous.delete.assert_not_awaited()
            item.repository.set_panel_message_id.assert_not_awaited()
        finally:
            await item.panel.close()

    asyncio.run(run())


def test_failed_save_can_finish_without_another_post():
    async def run():
        item = setup_panel()
        item.repository.set_panel_message_id.side_effect = [
            RuntimeError("Save failed"),
            None,
        ]

        try:
            with pytest.raises(RuntimeError, match="Save failed"):
                await item.panel.move_to_bottom()

            item.previous.delete.assert_not_awaited()

            item.repository.read.return_value = 500
            item.channel.fetch_message.return_value = item.new
            await item.panel.ensure()

            item.channel.send.assert_awaited_once()
            assert item.repository.set_panel_message_id.await_count == 2
            item.previous.delete.assert_awaited_once()
            item.new.edit.assert_awaited_once()
        finally:
            await item.panel.close()

    asyncio.run(run())


def test_failed_delete_can_be_retried_without_another_post():
    async def run():
        item = setup_panel()
        item.previous.delete.side_effect = [
            RuntimeError("Delete failed"),
            None,
        ]

        try:
            with pytest.raises(RuntimeError, match="Delete failed"):
                await item.panel.move_to_bottom()

            item.repository.set_panel_message_id.assert_awaited_once_with(
                500
            )

            item.repository.read.return_value = 500
            item.channel.fetch_message.return_value = item.new
            await item.panel.ensure()

            item.channel.send.assert_awaited_once()
            assert item.previous.delete.await_count == 2
        finally:
            await item.panel.close()

    asyncio.run(run())


def test_foreign_saved_message_is_not_deleted():
    async def run():
        item = setup_panel()
        item.previous.author.id = 999

        try:
            with pytest.raises(RecruitmentPanelTargetError):
                await item.panel.move_to_bottom()

            item.channel.send.assert_not_awaited()
            item.previous.delete.assert_not_awaited()
        finally:
            await item.panel.close()

    asyncio.run(run())


def test_missing_old_panel_does_not_prevent_new_panel():
    async def run():
        item = setup_panel()
        response = SimpleNamespace(status=404, reason="Not Found")
        item.channel.fetch_message.side_effect = discord.NotFound(
            response,
            {"code": 10008, "message": "Unknown Message"},
        )

        try:
            await item.panel.move_to_bottom()

            item.channel.send.assert_awaited_once()
            item.repository.set_panel_message_id.assert_awaited_once_with(
                500
            )
            item.previous.delete.assert_not_awaited()
        finally:
            await item.panel.close()

    asyncio.run(run())


def test_closed_panel_cannot_move():
    async def run():
        item = setup_panel()
        await item.panel.close()

        with pytest.raises(RuntimeError):
            await item.panel.move_to_bottom()

        item.channel.send.assert_not_awaited()

    asyncio.run(run())
