import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from wanbot.bump.panel import BumpPanelView, command_guide, panel_embed
from wanbot.bump.repository import BumpState


NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def waiting_state():
    return BumpState(
        last_success_message_id=500,
        last_bumped_at=NOW,
        next_bump_at=NOW + timedelta(hours=2),
    )


def make_view(handler, available=True, guild_id=100):
    return BumpPanelView(
        guild_id=guild_id,
        channel_id=200,
        available=available,
        on_guide=handler,
    )


def make_interaction(guild_id=100, channel_id=200):
    return SimpleNamespace(
        guild_id=guild_id,
        channel_id=channel_id,
        response=SimpleNamespace(
            is_done=Mock(return_value=False),
            send_message=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def test_initial_panel_is_available():
    embed = panel_embed(BumpState(), now=NOW)

    assert embed.title == "🟢 BUMPできます"
    assert embed.color == discord.Color.green()


def test_waiting_panel_shows_saved_deadline():
    state = waiting_state()
    embed = panel_embed(state, now=NOW)
    timestamp = int(state.next_bump_at.timestamp())

    assert embed.title == "⏳ BUMP待機中"
    assert f"<t:{timestamp}:F>" in embed.description
    assert f"<t:{timestamp}:R>" in embed.description


def test_expired_deadline_displays_available_without_erasing_history():
    state = waiting_state()

    embed = panel_embed(state, now=state.next_bump_at)

    assert embed.title == "🟢 BUMPできます"
    assert state.last_success_message_id == 500
    assert state.next_bump_at is not None


def test_guide_without_command_id_explains_manual_selection():
    text = command_guide(None)

    assert "/bump" in text
    assert "DISBOARD" in text
    assert "選んで実行" in text


def test_guide_with_command_id_contains_command_link():
    assert "</bump:123456789>" in command_guide(123456789)


def test_waiting_button_is_disabled():
    async def scenario():
        view = make_view(AsyncMock(), available=False)
        try:
            assert view.open_command.disabled
            assert view.open_command.label == "BUMP待機中"
            assert view.is_persistent()
        finally:
            view.stop()

    asyncio.run(scenario())


def test_available_button_invokes_guide():
    async def scenario():
        handler = AsyncMock()
        view = make_view(handler)
        interaction = make_interaction()
        try:
            assert not view.open_command.disabled
            assert view.open_command.label == "BUMPする"

            await view.open_command.callback(interaction)

            handler.assert_awaited_once_with(interaction)
        finally:
            view.stop()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("guild_id", "channel_id"),
    [(999, 200), (100, 999), (None, 200)],
)
def test_wrong_location_does_not_invoke_guide(guild_id, channel_id):
    async def scenario():
        handler = AsyncMock()
        view = make_view(handler)
        interaction = make_interaction(guild_id, channel_id)
        try:
            await view.open_command.callback(interaction)

            handler.assert_not_awaited()
            interaction.response.send_message.assert_awaited_once()
            assert interaction.response.send_message.call_args.kwargs["ephemeral"]
        finally:
            view.stop()

    asyncio.run(scenario())


def test_button_ids_are_stable_and_separate_between_guilds():
    async def scenario():
        first = make_view(AsyncMock())
        restored = make_view(AsyncMock())
        other = make_view(AsyncMock(), guild_id=999)
        try:
            assert first.open_command.custom_id == restored.open_command.custom_id
            assert first.open_command.custom_id != other.open_command.custom_id
            assert len(first.open_command.custom_id) <= 100
            assert first.is_persistent()
        finally:
            first.stop()
            restored.stop()
            other.stop()

    asyncio.run(scenario())
