import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from wanbot.recruitment.forms import (
    FormContext,
    RecruitmentModal,
    RecruitmentTargetView,
)


def setup_form():
    available = Mock(return_value=True)
    context = FormContext(
        guild_id=100,
        channel_id=200,
        user_id=300,
        is_available=available,
    )
    interaction = SimpleNamespace(
        guild_id=100,
        channel_id=200,
        # ロール情報がなくても操作できることを確認する。
        user=SimpleNamespace(id=300),
        response=SimpleNamespace(
            is_done=Mock(return_value=False),
            send_message=AsyncMock(),
            send_modal=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    handler = AsyncMock()
    return context, available, interaction, handler


def fill_modal(modal, *, modes="アンレート", player_count="あと2人"):
    # 入力欄の値だけを代用品に置き換える。
    modal.modes = SimpleNamespace(value=modes)
    modal.player_count = SimpleNamespace(value=player_count)
    modal.party = SimpleNamespace(value="")
    modal.comment = SimpleNamespace(value="よろしくお願いします")


def test_both_roles_can_be_selected_without_owned_role_check():
    async def run():
        context, available, interaction, handler = setup_form()
        view = RecruitmentTargetView(context=context, on_draft=handler)
        modal = None

        try:
            assert view.select_targets.min_values == 1
            assert view.select_targets.max_values == 2

            await view.open_form(
                interaction,
                frozenset({"gachi", "enjoy"}),
            )

            interaction.response.send_modal.assert_awaited_once()
            modal = interaction.response.send_modal.call_args.args[0]
            assert modal.targets == frozenset({"gachi", "enjoy"})
            handler.assert_not_awaited()
        finally:
            view.stop()
            if modal is not None:
                modal.stop()

    asyncio.run(run())


@pytest.mark.parametrize(
    "scope",
    ["other_user", "other_guild", "other_channel"],
)
def test_wrong_scope_cannot_open_form(scope):
    async def run():
        context, available, interaction, handler = setup_form()

        if scope == "other_user":
            interaction.user.id = 999
        elif scope == "other_guild":
            interaction.guild_id = 999
        else:
            interaction.channel_id = 999

        view = RecruitmentTargetView(context=context, on_draft=handler)
        try:
            await view.open_form(interaction, frozenset({"gachi"}))

            interaction.response.send_modal.assert_not_awaited()
            interaction.response.send_message.assert_awaited_once()
            kwargs = interaction.response.send_message.call_args.kwargs
            assert kwargs["ephemeral"] is True
        finally:
            view.stop()

    asyncio.run(run())


def test_unavailable_feature_cannot_open_form():
    async def run():
        context, available, interaction, handler = setup_form()
        available.return_value = False
        view = RecruitmentTargetView(context=context, on_draft=handler)

        try:
            await view.open_form(interaction, frozenset({"enjoy"}))

            interaction.response.send_modal.assert_not_awaited()
            interaction.response.send_message.assert_awaited_once()
        finally:
            view.stop()

    asyncio.run(run())


def test_invalid_target_does_not_open_form():
    async def run():
        context, available, interaction, handler = setup_form()
        view = RecruitmentTargetView(context=context, on_draft=handler)

        try:
            await view.open_form(interaction, frozenset({"unknown"}))

            interaction.response.send_modal.assert_not_awaited()
            handler.assert_not_awaited()
        finally:
            view.stop()

    asyncio.run(run())


def test_modal_passes_validated_draft_to_next_step():
    async def run():
        context, available, interaction, handler = setup_form()
        modal = RecruitmentModal(
            context=context,
            targets=frozenset({"enjoy"}),
            on_draft=handler,
        )

        try:
            assert modal.modes.required is True
            assert modal.player_count.required is True
            assert modal.party.required is False
            assert modal.comment.required is False

            fill_modal(modal, modes="  アンレート、スイフト  ")
            await modal.on_submit(interaction)

            handler.assert_awaited_once()
            received_interaction, draft = handler.call_args.args
            assert received_interaction is interaction
            assert draft.targets == frozenset({"enjoy"})
            assert draft.modes == "アンレート、スイフト"
            assert draft.player_count == "あと2人"
            assert draft.party == ""
        finally:
            modal.stop()

    asyncio.run(run())


def test_blank_required_input_is_not_forwarded():
    async def run():
        context, available, interaction, handler = setup_form()
        modal = RecruitmentModal(
            context=context,
            targets=frozenset({"gachi"}),
            on_draft=handler,
        )

        try:
            fill_modal(modal, modes="   ")
            await modal.on_submit(interaction)

            handler.assert_not_awaited()
            interaction.response.send_message.assert_awaited_once()
        finally:
            modal.stop()

    asyncio.run(run())


def test_modal_rechecks_availability_at_submission():
    async def run():
        context, available, interaction, handler = setup_form()
        modal = RecruitmentModal(
            context=context,
            targets=frozenset({"enjoy"}),
            on_draft=handler,
        )

        try:
            fill_modal(modal)
            available.return_value = False

            await modal.on_submit(interaction)

            handler.assert_not_awaited()
            interaction.response.send_message.assert_awaited_once()
        finally:
            modal.stop()

    asyncio.run(run())


def test_modal_rejects_submission_by_another_user():
    async def run():
        context, available, interaction, handler = setup_form()
        modal = RecruitmentModal(
            context=context,
            targets=frozenset({"enjoy"}),
            on_draft=handler,
        )

        try:
            fill_modal(modal)
            interaction.user.id = 999

            await modal.on_submit(interaction)

            handler.assert_not_awaited()
        finally:
            modal.stop()

    asyncio.run(run())
