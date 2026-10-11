import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from wanbot.recruitment.confirmation import (
    RecruitmentConfirmView,
    send_confirmation,
)
from wanbot.recruitment.forms import FormContext
from wanbot.recruitment.models import make_draft


def make_interaction():
    return SimpleNamespace(
        guild_id=100,
        channel_id=200,
        user=SimpleNamespace(id=300),
        response=SimpleNamespace(
            is_done=Mock(return_value=False),
            send_message=AsyncMock(),
            defer=AsyncMock(),
            edit_message=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


def setup_confirmation():
    available = Mock(return_value=True)
    context = FormContext(
        guild_id=100,
        channel_id=200,
        user_id=300,
        is_available=available,
    )
    draft = make_draft(
        targets=frozenset({"gachi", "enjoy"}),
        modes="コンペ",
        player_count="あと2人",
    )
    publish = AsyncMock()
    view = RecruitmentConfirmView(
        context=context,
        draft=draft,
        on_publish=publish,
    )
    return view, context, draft, publish, available


def test_preview_is_private_and_does_not_publish():
    async def run():
        view, context, draft, publish, available = setup_confirmation()
        view.stop()
        interaction = make_interaction()
        preview = None

        try:
            preview = await send_confirmation(
                interaction,
                draft,
                context=context,
                gachi_role_id=400,
                enjoy_role_id=500,
                on_publish=publish,
            )

            publish.assert_not_awaited()
            kwargs = interaction.response.send_message.call_args.kwargs
            assert kwargs["ephemeral"] is True
            assert kwargs["allowed_mentions"].roles is False
            assert kwargs["allowed_mentions"].everyone is False
            assert kwargs["allowed_mentions"].users is False
            assert "まだ投稿されていません" in kwargs["content"]
            assert kwargs["view"] is preview
        finally:
            if preview is not None:
                preview.stop()

    asyncio.run(run())


def test_complete_publishes_once_and_removes_buttons():
    async def run():
        view, context, draft, publish, available = setup_confirmation()
        interaction = make_interaction()

        try:
            await view.complete.callback(interaction)

            publish.assert_awaited_once_with(interaction, draft)
            interaction.response.defer.assert_awaited_once()
            kwargs = interaction.edit_original_response.call_args.kwargs
            assert kwargs["view"] is None
            assert kwargs["content"] == "募集を投稿しました。"

            await view.complete.callback(make_interaction())
            publish.assert_awaited_once()
        finally:
            view.stop()

    asyncio.run(run())


def test_cancel_does_not_publish():
    async def run():
        view, context, draft, publish, available = setup_confirmation()
        interaction = make_interaction()

        try:
            await view.cancel.callback(interaction)

            publish.assert_not_awaited()
            kwargs = interaction.response.edit_message.call_args.kwargs
            assert kwargs["view"] is None
            assert kwargs["content"] == "募集をキャンセルしました。"

            await view.complete.callback(make_interaction())
            publish.assert_not_awaited()
        finally:
            view.stop()

    asyncio.run(run())


@pytest.mark.parametrize("button_name", ["complete", "cancel"])
def test_other_user_cannot_operate_confirmation(button_name):
    async def run():
        view, context, draft, publish, available = setup_confirmation()
        interaction = make_interaction()
        interaction.user.id = 999

        try:
            await getattr(view, button_name).callback(interaction)

            publish.assert_not_awaited()
            interaction.response.send_message.assert_awaited_once()
            interaction.response.edit_message.assert_not_awaited()
        finally:
            view.stop()

    asyncio.run(run())


def test_stopped_feature_does_not_publish():
    async def run():
        view, context, draft, publish, available = setup_confirmation()
        available.return_value = False

        try:
            await view.complete.callback(make_interaction())
            publish.assert_not_awaited()
        finally:
            view.stop()

    asyncio.run(run())


def test_repeated_click_and_cancel_during_publish_are_rejected():
    async def run():
        view, context, draft, publish, available = setup_confirmation()
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_publish(interaction, draft):
            started.set()
            await release.wait()

        publish.side_effect = slow_publish
        task = asyncio.create_task(
            view.complete.callback(make_interaction())
        )

        try:
            await asyncio.wait_for(started.wait(), timeout=2)

            second = make_interaction()
            cancel = make_interaction()

            await view.complete.callback(second)
            await view.cancel.callback(cancel)

            publish.assert_awaited_once()
            second.response.send_message.assert_awaited_once()
            cancel.response.send_message.assert_awaited_once()
            cancel.response.edit_message.assert_not_awaited()
        finally:
            release.set()
            await task
            view.stop()

    asyncio.run(run())


def test_failed_publish_is_not_retried_from_same_view():
    async def run():
        view, context, draft, publish, available = setup_confirmation()
        publish.side_effect = RuntimeError("Send failed")
        interaction = make_interaction()

        try:
            with patch(
                "wanbot.recruitment.confirmation.log_event"
            ) as logger:
                await view.complete.callback(interaction)
                logger.assert_called_once()

            kwargs = interaction.edit_original_response.call_args.kwargs
            assert kwargs["view"] is None
            assert "確認できませんでした" in kwargs["content"]

            await view.complete.callback(make_interaction())
            publish.assert_awaited_once()
        finally:
            view.stop()

    asyncio.run(run())
