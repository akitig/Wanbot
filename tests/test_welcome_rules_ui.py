import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from wanbot.welcome.questionnaire_controller import QuestionnaireController
from wanbot.welcome.questionnaire_forms import QuestionnaireContext
from wanbot.welcome.rules_ui import add_rules_buttons


def make_case(*, confirmed=True, status="completed"):
    context = QuestionnaireContext(
        guild_id=100,
        channel_id=500,
        user_id=300,
        session_id="a" * 32,
        is_available=lambda: True,
    )
    session = SimpleNamespace(
        guild_id=100,
        channel_id=500,
        user_id=300,
        session_id="a" * 32,
        state="active",
    )
    snapshot = SimpleNamespace(
        session_id="a" * 32,
        user_id=300,
        revision=7,
        progress=SimpleNamespace(
            status=status,
            name="テスト",
            gender="no_answer",
            adult=True,
            main_game="VALORANT",
            comment="よろしくお願いします",
            hobby="散歩",
            plays_valo=True,
        ),
    )
    checker = SimpleNamespace(
        message_url="https://discord.com/channels/100/500/600",
        has_reacted=AsyncMock(return_value=confirmed),
    )
    welcome = SimpleNamespace(get_active=AsyncMock(return_value=session))
    controller = QuestionnaireController(
        context=context,
        welcome_repository=welcome,
        questionnaire_repository=SimpleNamespace(
            ensure=AsyncMock(return_value=snapshot),
        ),
        lock=asyncio.Lock(),
        rules_checker=checker,
    )
    interaction = SimpleNamespace(
        guild_id=100,
        channel_id=500,
        user=SimpleNamespace(id=300),
        response=SimpleNamespace(
            is_done=lambda: True,
            defer=AsyncMock(),
            send_message=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )
    return controller, checker, welcome, interaction, snapshot


@pytest.mark.parametrize("confirmed", [True, False])
def test_confirmation_result_is_displayed(confirmed):
    async def scenario():
        controller, checker, _, interaction, _ = make_case(
            confirmed=confirmed
        )
        try:
            await controller.check_rules(interaction)
            checker.has_reacted.assert_awaited_once_with(300)
            content = interaction.edit_original_response.call_args.kwargs[
                "content"
            ]
            expected = (
                "ルールへのリアクションを確認しました"
                if confirmed
                else "指定のリアクションをまだ確認できません"
            )
            assert expected in content
        finally:
            controller.close()

    asyncio.run(scenario())


def test_another_user_cannot_confirm():
    async def scenario():
        controller, checker, _, interaction, _ = make_case()
        interaction.user.id = 301
        try:
            await controller.check_rules(interaction)
            checker.has_reacted.assert_not_awaited()
            interaction.edit_original_response.assert_not_awaited()
        finally:
            controller.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("status", ["answering", "age_review_required"])
def test_incomplete_answers_cannot_confirm(status):
    async def scenario():
        controller, checker, _, interaction, _ = make_case(status=status)
        try:
            await controller.check_rules(interaction)
            checker.has_reacted.assert_not_awaited()
        finally:
            controller.close()

    asyncio.run(scenario())


def test_closed_session_cannot_confirm():
    async def scenario():
        controller, checker, welcome, interaction, _ = make_case()
        welcome.get_active.return_value = None
        try:
            await controller.check_rules(interaction)
            checker.has_reacted.assert_not_awaited()
        finally:
            controller.close()

    asyncio.run(scenario())


def test_failed_check_does_not_display_success():
    async def scenario():
        controller, checker, _, interaction, _ = make_case()
        checker.has_reacted.side_effect = RuntimeError("Unavailable")
        try:
            await controller.check_rules(interaction)
            content = interaction.edit_original_response.call_args.kwargs[
                "content"
            ]
            assert "処理を完了できませんでした" in content
            assert "リアクションを確認しました" not in content
        finally:
            controller.close()

    asyncio.run(scenario())


def test_completed_screen_keeps_edit_menu_and_adds_rules_buttons():
    async def scenario():
        controller, checker, _, interaction, snapshot = make_case()
        try:
            await controller._render(interaction, snapshot)
            view = interaction.edit_original_response.call_args.kwargs["view"]
            assert any(isinstance(item, discord.ui.Select) for item in view.children)
            assert any(
                isinstance(item, discord.ui.Button)
                and item.url == checker.message_url
                for item in view.children
            )
            assert any(
                getattr(item, "label", None) == "リアクションを押しました"
                for item in view.children
            )
        finally:
            controller.close()

    asyncio.run(scenario())


def test_button_defers_before_checking():
    async def scenario():
        controller, checker, _, interaction, _ = make_case()
        view = discord.ui.View()
        calls = []

        async def defer(**kwargs):
            calls.append("defer")

        async def check(received):
            assert received is interaction
            calls.append("check")

        interaction.response.defer.side_effect = defer
        try:
            add_rules_buttons(
                view,
                context=controller.context,
                checker=checker,
                on_check=check,
            )
            await view.children[-1].callback(interaction)
            assert calls == ["defer", "check"]
        finally:
            view.stop()
            controller.close()

    asyncio.run(scenario())
