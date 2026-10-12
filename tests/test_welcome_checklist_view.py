import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from wanbot.welcome.checklist_view import (
    ChecklistState,
    WelcomeChecklistView,
)


def make_view(state):
    context = SimpleNamespace(
        guild_id=100,
        session_id="a" * 32,
        check=AsyncMock(return_value=True),
    )
    handlers = SimpleNamespace(
        introduction=AsyncMock(),
        diagnosis=AsyncMock(),
        interview=AsyncMock(),
    )
    view = WelcomeChecklistView(
        context=context,
        state=state,
        rules_url="https://discord.com/channels/100/500/600",
        rules_emoji="👍",
        on_introduction=handlers.introduction,
        on_diagnosis=handlers.diagnosis,
        on_interview=handlers.interview,
    )
    return view, context, handlers


def buttons(view):
    return [
        item
        for row in view.children
        if isinstance(row, discord.ui.ActionRow)
        for item in row.children
        if isinstance(item, discord.ui.Button)
    ]


def text(view):
    return "\n".join(
        item.content
        for item in view.children
        if isinstance(item, discord.ui.TextDisplay)
    )


def test_initial_screen_has_separate_buttons():
    async def scenario():
        view, _, _ = make_view(ChecklistState())
        try:
            items = buttons(view)
            assert [item.label for item in items] == [
                "はじめる", "ルールはこちら", "はじめる", "はじめる"
            ]
            assert items[0].disabled is False
            assert items[2].disabled is True
            assert "□ 自己紹介を入力する" in text(view)
            assert "□ ルールを読んで👍をする" in text(view)
            assert view.is_persistent()
        finally:
            view.stop()

    asyncio.run(scenario())


def test_completed_screen_shows_checks_and_interview():
    async def scenario():
        state = ChecklistState(
            introduction_done=True,
            rules_done=True,
            plays_valorant=True,
            diagnosis_done=True,
        )
        view, _, _ = make_view(state)
        try:
            assert text(view).count("✅") == 3
            assert [item.label for item in buttons(view)] == [
                "確認・修正する",
                "ルールはこちら",
                "結果を見る",
                "はじめる",
            ]
        finally:
            view.stop()

    asyncio.run(scenario())


def test_non_valorant_member_does_not_get_diagnosis_button():
    async def scenario():
        state = ChecklistState(
            introduction_done=True,
            rules_done=True,
            plays_valorant=False,
        )
        view, _, _ = make_view(state)
        try:
            assert "対象外" in text(view)
            assert state.interview_ready
            assert len(buttons(view)) == 3
        finally:
            view.stop()

    asyncio.run(scenario())


@pytest.mark.parametrize("rules_done", [False, None])
def test_unconfirmed_rules_hide_interview(rules_done):
    async def scenario():
        state = ChecklistState(
            introduction_done=True,
            rules_done=rules_done,
            plays_valorant=True,
            diagnosis_done=True,
        )
        view, _, _ = make_view(state)
        try:
            assert not state.interview_ready
            assert buttons(view)[-1].disabled is True
            if rules_done is None:
                assert "状態を確認できません" in text(view)
        finally:
            view.stop()

    asyncio.run(scenario())


def test_other_user_cannot_open_introduction():
    async def scenario():
        view, context, handlers = make_view(ChecklistState())
        context.check.return_value = False
        interaction = SimpleNamespace(
            response=SimpleNamespace(defer=AsyncMock())
        )
        try:
            await buttons(view)[0].callback(interaction)
            handlers.introduction.assert_not_awaited()
            interaction.response.defer.assert_not_awaited()
        finally:
            view.stop()

    asyncio.run(scenario())


def test_introduction_opens_private_response():
    async def scenario():
        view, _, handlers = make_view(ChecklistState())
        interaction = SimpleNamespace(
            response=SimpleNamespace(defer=AsyncMock())
        )
        try:
            await buttons(view)[0].callback(interaction)
            interaction.response.defer.assert_awaited_once_with(
                ephemeral=True, thinking=True
            )
            handlers.introduction.assert_awaited_once_with(interaction)
        finally:
            view.stop()

    asyncio.run(scenario())
