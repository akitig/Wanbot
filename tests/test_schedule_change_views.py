import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from wanbot.welcome.schedule_change_views import ScheduleChangeMenu


def interaction(user_id=300):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        response=SimpleNamespace(
            is_done=Mock(return_value=False),
            defer=AsyncMock(),
        ),
        edit_original_response=AsyncMock(),
    )


def setup_menu():
    authorize = AsyncMock(return_value=True)
    confirm = AsyncMock()
    error = AsyncMock()
    expected = ("submitted-at", "proposal-id")
    menu = ScheduleChangeMenu(
        expected=expected,
        authorize=authorize,
        on_confirm=confirm,
        on_error=error,
    )
    return menu, authorize, confirm, expected


def confirmation_view(current):
    return current.edit_original_response.await_args.kwargs["view"]


def test_menu_labels():
    async def run():
        menu, _, _, _ = setup_menu()
        try:
            assert [item.label for item in menu.children] == [
                "日程を変更する",
                "希望日時の再提出を依頼する",
            ]
        finally:
            menu.stop()

    asyncio.run(run())


def test_unauthorized_user_cannot_open_confirmation():
    async def run():
        menu, authorize, confirm, _ = setup_menu()
        authorize.return_value = False
        current = interaction()
        try:
            await menu.children[1].callback(current)
            authorize.assert_awaited_once_with(current, "manager")
            current.edit_original_response.assert_not_awaited()
            confirm.assert_not_awaited()
        finally:
            menu.stop()

    asyncio.run(run())


def test_confirm_rechecks_authorization_and_keeps_target():
    async def run():
        menu, authorize, confirm, expected = setup_menu()
        current = interaction()
        try:
            await menu.children[0].callback(current)
            view = confirmation_view(current)
            clicked = interaction()
            await view.children[0].callback(clicked)

            assert authorize.await_count == 2
            confirm.assert_awaited_once_with(clicked, "member", expected)
            assert view.is_finished()
        finally:
            menu.stop()

    asyncio.run(run())


def test_lost_permission_blocks_confirmation():
    async def run():
        menu, authorize, confirm, _ = setup_menu()
        current = interaction()
        try:
            await menu.children[1].callback(current)
            view = confirmation_view(current)
            authorize.return_value = False
            await view.children[0].callback(interaction())
            confirm.assert_not_awaited()
        finally:
            menu.stop()

    asyncio.run(run())


def test_other_user_cannot_confirm():
    async def run():
        menu, authorize, confirm, _ = setup_menu()
        current = interaction()
        try:
            await menu.children[0].callback(current)
            view = confirmation_view(current)
            await view.children[0].callback(interaction(999))
            assert authorize.await_count == 1
            confirm.assert_not_awaited()
        finally:
            menu.stop()

    asyncio.run(run())


def test_cancel_does_not_change_schedule():
    async def run():
        menu, _, confirm, _ = setup_menu()
        current = interaction()
        try:
            await menu.children[0].callback(current)
            view = confirmation_view(current)
            await view.children[1].callback(interaction())
            confirm.assert_not_awaited()
            assert view.is_finished()
        finally:
            menu.stop()

    asyncio.run(run())


def test_finished_confirmation_does_not_execute_twice():
    async def run():
        menu, _, confirm, _ = setup_menu()
        current = interaction()
        try:
            await menu.children[0].callback(current)
            view = confirmation_view(current)
            await view.children[0].callback(interaction())
            await view.children[0].callback(interaction())
            assert confirm.await_count == 1
        finally:
            menu.stop()

    asyncio.run(run())


def test_stopping_menu_stops_confirmation():
    async def run():
        menu, _, _, _ = setup_menu()
        current = interaction()
        await menu.children[0].callback(current)
        view = confirmation_view(current)
        menu.stop()
        assert view.is_finished()

    asyncio.run(run())
