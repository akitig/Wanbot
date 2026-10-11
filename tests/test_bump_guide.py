import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from wanbot.bump.guide import BumpGuideController
from wanbot.bump.repository import BumpState


NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def setup_guide(*, state=None, command_id=None):
    repository = SimpleNamespace(
        read=AsyncMock(
            return_value=state if state is not None else BumpState()
        ),
    )
    available = Mock(return_value=True)

    controller = BumpGuideController(
        guild_id=100,
        channel_id=200,
        repository=repository,
        is_available=available,
        command_id=command_id,
        clock=lambda: NOW,
    )

    interaction = SimpleNamespace(
        guild_id=100,
        channel_id=200,
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )

    return controller, repository, available, interaction


def reply_text(interaction):
    return interaction.followup.send.call_args.args[0]


def test_available_guide_is_ephemeral_and_has_no_mentions():
    async def run():
        controller, repository, available, interaction = setup_guide()

        await controller.handle(interaction)

        interaction.response.defer.assert_awaited_once_with(
            ephemeral=True,
            thinking=True,
        )
        repository.read.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()
        assert "/bump" in reply_text(interaction)

        kwargs = interaction.followup.send.call_args.kwargs
        assert kwargs["ephemeral"] is True
        assert kwargs["allowed_mentions"].everyone is False
        assert kwargs["allowed_mentions"].roles is False
        assert kwargs["allowed_mentions"].users is False

    asyncio.run(run())


def test_configured_command_id_is_used():
    async def run():
        controller, repository, available, interaction = setup_guide(
            command_id=123456789
        )

        await controller.handle(interaction)

        assert "</bump:123456789>" in reply_text(interaction)

    asyncio.run(run())


def test_waiting_state_does_not_show_command_guide():
    async def run():
        deadline = NOW + timedelta(hours=1)
        state = BumpState(
            last_success_message_id=500,
            last_bumped_at=NOW - timedelta(hours=1),
            next_bump_at=deadline,
        )
        controller, repository, available, interaction = setup_guide(
            state=state
        )

        await controller.handle(interaction)

        text = reply_text(interaction)
        assert "待機中" in text
        assert str(int(deadline.timestamp())) in text
        assert "/bump" not in text

    asyncio.run(run())


def test_wrong_guild_is_rejected_without_reading_data():
    async def run():
        controller, repository, available, interaction = setup_guide()
        interaction.guild_id = 999

        await controller.handle(interaction)

        repository.read.assert_not_awaited()
        assert "利用できません" in reply_text(interaction)

    asyncio.run(run())


def test_wrong_channel_is_rejected_without_reading_data():
    async def run():
        controller, repository, available, interaction = setup_guide()
        interaction.channel_id = 999

        await controller.handle(interaction)

        repository.read.assert_not_awaited()
        assert "利用できません" in reply_text(interaction)

    asyncio.run(run())


def test_stopped_feature_is_rejected_without_reading_data():
    async def run():
        controller, repository, available, interaction = setup_guide()
        available.return_value = False

        await controller.handle(interaction)

        repository.read.assert_not_awaited()
        assert "利用できません" in reply_text(interaction)

    asyncio.run(run())


def test_feature_stopped_during_read_does_not_show_guide():
    async def run():
        controller, repository, available, interaction = setup_guide()
        available.side_effect = [True, False]

        await controller.handle(interaction)

        repository.read.assert_awaited_once()
        text = reply_text(interaction)
        assert "利用できません" in text
        assert "/bump" not in text

    asyncio.run(run())


def test_read_failure_is_propagated_without_showing_guide():
    async def run():
        controller, repository, available, interaction = setup_guide()
        repository.read.side_effect = RuntimeError("Read failed")

        with pytest.raises(RuntimeError, match="Read failed"):
            await controller.handle(interaction)

        interaction.followup.send.assert_not_awaited()

    asyncio.run(run())
