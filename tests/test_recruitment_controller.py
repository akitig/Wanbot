import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from wanbot.config import parse_config
from wanbot.recruitment.controller import RecruitmentController
from wanbot.recruitment.forms import RecruitmentTargetView
from wanbot.recruitment.panel import RecruitmentPanelView


def make_interaction():
    return SimpleNamespace(
        guild_id=100,
        channel_id=200,
        user=SimpleNamespace(id=300, bot=False),
        response=SimpleNamespace(
            is_done=Mock(return_value=False),
            send_message=AsyncMock(),
            send_modal=AsyncMock(),
            defer=AsyncMock(),
            edit_message=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


def setup_controller():
    config = parse_config({
        "DISCORD_TOKEN": "test-token",
        "TEST_GUILD_ID": "100",
        "TEST_ENABLED_FEATURES": "recruitment",
        "TEST_VALO_RECRUIT_CHANNEL_ID": "200",
        "TEST_GACHI_ROLE_ID": "400",
        "TEST_ENJOY_ROLE_ID": "500",
    })
    publisher = SimpleNamespace(publish=AsyncMock())
    available = Mock(return_value=True)

    controller = RecruitmentController(
        guild=config.guild_for(100),
        publisher=publisher,
        is_available=available,
    )
    return controller, publisher, available


def test_panel_has_persistent_scoped_button():
    async def run():
        panel = RecruitmentPanelView(
            guild_id=100,
            channel_id=200,
            on_begin=AsyncMock(),
        )
        try:
            assert panel.is_persistent()
            assert panel.begin.custom_id == (
                "wanbot:recruitment:begin:100:200"
            )
            assert panel.begin.label == "VALORANTの募集を行う"
        finally:
            panel.stop()

    asyncio.run(run())


def test_panel_calls_begin_handler():
    async def run():
        handler = AsyncMock()
        panel = RecruitmentPanelView(
            guild_id=100,
            channel_id=200,
            on_begin=handler,
        )
        interaction = make_interaction()

        try:
            await panel.begin.callback(interaction)
            handler.assert_awaited_once_with(interaction)
        finally:
            panel.stop()

    asyncio.run(run())


def test_begin_shows_private_target_selection():
    async def run():
        controller, publisher, available = setup_controller()
        interaction = make_interaction()

        try:
            await controller.begin(interaction)

            kwargs = interaction.response.send_message.call_args.kwargs
            assert kwargs["ephemeral"] is True
            assert isinstance(kwargs["view"], RecruitmentTargetView)
            assert kwargs["view"].context.user_id == 300
            assert kwargs["allowed_mentions"].roles is False
            publisher.publish.assert_not_awaited()
        finally:
            controller.close()

    asyncio.run(run())


@pytest.mark.parametrize("field", ["guild_id", "channel_id"])
def test_begin_rejects_other_scope(field):
    async def run():
        controller, publisher, available = setup_controller()
        interaction = make_interaction()
        setattr(interaction, field, 999)

        try:
            await controller.begin(interaction)

            kwargs = interaction.response.send_message.call_args.kwargs
            assert "view" not in kwargs
            publisher.publish.assert_not_awaited()
        finally:
            controller.close()

    asyncio.run(run())


def test_input_flows_to_confirmation_then_publish():
    async def run():
        controller, publisher, available = setup_controller()
        modal = None

        try:
            beginning = make_interaction()
            await controller.begin(beginning)
            target_view = (
                beginning.response.send_message.call_args.kwargs["view"]
            )

            selection = make_interaction()
            await target_view.open_form(
                selection,
                frozenset({"gachi", "enjoy"}),
            )
            modal = selection.response.send_modal.call_args.args[0]

            modal.modes = SimpleNamespace(value="コンペ、アンレート")
            modal.player_count = SimpleNamespace(value="あと2人")
            modal.party = SimpleNamespace(value="ABC123")
            modal.comment = SimpleNamespace(value="気軽にどうぞ")

            submitted = make_interaction()
            await modal.on_submit(submitted)

            publisher.publish.assert_not_awaited()
            preview_kwargs = (
                submitted.response.send_message.call_args.kwargs
            )
            assert preview_kwargs["ephemeral"] is True
            confirmation = preview_kwargs["view"]

            completed = make_interaction()
            await confirmation.complete.callback(completed)

            publisher.publish.assert_awaited_once()
            interaction, draft = publisher.publish.call_args.args
            assert interaction is completed
            assert draft.targets == frozenset({"gachi", "enjoy"})
            assert draft.modes == "コンペ、アンレート"
            assert draft.player_count == "あと2人"
            assert draft.party == "ABC123"
            assert draft.comment == "気軽にどうぞ"
        finally:
            if modal is not None:
                modal.stop()
            controller.close()

    asyncio.run(run())


def test_unavailable_feature_does_not_open_selection():
    async def run():
        controller, publisher, available = setup_controller()
        available.return_value = False
        interaction = make_interaction()

        try:
            await controller.begin(interaction)

            kwargs = interaction.response.send_message.call_args.kwargs
            assert "view" not in kwargs
            publisher.publish.assert_not_awaited()
        finally:
            controller.close()

    asyncio.run(run())


def test_close_stops_view_and_rejects_further_operations():
    async def run():
        controller, publisher, available = setup_controller()
        interaction = make_interaction()

        await controller.begin(interaction)
        view = interaction.response.send_message.call_args.kwargs["view"]

        controller.close()
        controller.close()

        assert view.is_finished()

        another = make_interaction()
        await controller.begin(another)

        kwargs = another.response.send_message.call_args.kwargs
        assert "view" not in kwargs
        publisher.publish.assert_not_awaited()

    asyncio.run(run())
