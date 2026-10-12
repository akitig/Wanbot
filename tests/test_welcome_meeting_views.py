import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from wanbot.welcome.meeting_views import (
    MeetingConfirmView,
    MeetingDateView,
    SecondManagerResponseView,
    SecondManagerView,
)


def interaction():
    return SimpleNamespace(
        response=SimpleNamespace(
            is_done=Mock(return_value=False),
            defer=AsyncMock(),
            send_message=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
    )


@pytest.mark.parametrize("preference", (1, 2, 3))
def test_date_button_passes_selected_preference(preference):
    async def scenario():
        authorize = AsyncMock(return_value=True)
        choose = AsyncMock()
        current = interaction()
        view = MeetingDateView(
            session_id="a" * 32,
            authorize=authorize,
            on_choose=choose,
        )
        try:
            await view.children[preference - 1].callback(current)

            authorize.assert_awaited_once_with(current)
            choose.assert_awaited_once_with(current, preference)
            current.response.defer.assert_awaited_once_with(
                ephemeral=True,
                thinking=True,
            )
        finally:
            view.stop()

    asyncio.run(scenario())


def test_denied_action_does_not_choose_date():
    async def scenario():
        choose = AsyncMock()
        view = MeetingDateView(
            session_id="a" * 32,
            authorize=AsyncMock(return_value=False),
            on_choose=choose,
        )
        try:
            await view.children[0].callback(interaction())
            choose.assert_not_awaited()
        finally:
            view.stop()

    asyncio.run(scenario())


def test_no_second_manager_passes_none():
    async def scenario():
        choose = AsyncMock()
        current = interaction()
        view = SecondManagerView(
            authorize=AsyncMock(return_value=True),
            on_choose=choose,
        )
        try:
            await view.children[1].callback(current)
            choose.assert_awaited_once_with(current, None)
        finally:
            view.stop()

    asyncio.run(scenario())


@pytest.mark.parametrize("accepted", (True, False))
def test_second_manager_response_keeps_proposal_id(accepted):
    async def scenario():
        respond = AsyncMock()
        current = interaction()
        proposal_id = "b" * 32
        view = SecondManagerResponseView(
            proposal_id=proposal_id,
            authorize=AsyncMock(return_value=True),
            on_respond=respond,
        )
        try:
            button = view.children[0 if accepted else 1]
            await button.callback(current)
            respond.assert_awaited_once_with(
                current,
                proposal_id,
                accepted,
            )
        finally:
            view.stop()

    asyncio.run(scenario())


def test_denied_response_is_not_forwarded():
    async def scenario():
        respond = AsyncMock()
        view = SecondManagerResponseView(
            proposal_id="b" * 32,
            authorize=AsyncMock(return_value=False),
            on_respond=respond,
        )
        try:
            await view.children[0].callback(interaction())
            respond.assert_not_awaited()
        finally:
            view.stop()

    asyncio.run(scenario())


def test_confirmation_requires_button_press():
    async def scenario():
        confirm = AsyncMock()
        back = AsyncMock()
        current = interaction()
        view = MeetingConfirmView(
            authorize=AsyncMock(return_value=True),
            on_confirm=confirm,
            on_back=back,
        )
        try:
            confirm.assert_not_awaited()
            await view.children[0].callback(current)
            confirm.assert_awaited_once_with(current)
            back.assert_not_awaited()
        finally:
            view.stop()

    asyncio.run(scenario())


def test_back_does_not_confirm():
    async def scenario():
        confirm = AsyncMock()
        back = AsyncMock()
        current = interaction()
        view = MeetingConfirmView(
            authorize=AsyncMock(return_value=True),
            on_confirm=confirm,
            on_back=back,
        )
        try:
            await view.children[1].callback(current)
            back.assert_awaited_once_with(current)
            confirm.assert_not_awaited()
        finally:
            view.stop()

    asyncio.run(scenario())


def test_shared_views_are_persistent_and_have_distinct_ids():
    async def scenario():
        date_view = MeetingDateView(
            session_id="a" * 32,
            authorize=AsyncMock(return_value=True),
            on_choose=AsyncMock(),
        )
        response_view = SecondManagerResponseView(
            proposal_id="b" * 32,
            authorize=AsyncMock(return_value=True),
            on_respond=AsyncMock(),
        )
        try:
            assert date_view.is_persistent()
            assert response_view.is_persistent()

            custom_ids = [
                item.custom_id
                for view in (date_view, response_view)
                for item in view.children
            ]
            assert len(custom_ids) == len(set(custom_ids))
        finally:
            date_view.stop()
            response_view.stop()

    asyncio.run(scenario())
