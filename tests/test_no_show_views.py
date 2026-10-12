"""不参加確認ボタンの対象者・場所・権限を検証する。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wanbot.welcome.no_show_views import NoShowView, no_show_message


def record(status="waiting"):
    return {
        "proposal_id": "b" * 32,
        "session_id": "a" * 32,
        "guild_id": 100,
        "user_id": 300,
        "channel_id": 500,
        "status": status,
        "deadline": (
            "2030-01-01T01:00:00+00:00"
            if status == "waiting" else None
        ),
    }


def interaction(*, user_id=300, guild_id=100, channel_id=500):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        guild_id=guild_id,
        channel_id=channel_id,
        response=SimpleNamespace(defer=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


@pytest.mark.parametrize("reschedule,index", [(True, 0), (False, 1)])
def test_applicant_answer_reaches_handler(reschedule, index):
    async def scenario():
        authorize = AsyncMock(return_value=True)
        handler = AsyncMock()
        view = NoShowView(
            record=record(),
            authorize=authorize,
            on_answer=handler,
        )
        current = interaction()
        try:
            await view.children[index].callback(current)
            authorize.assert_awaited_once_with(current)
            handler.assert_awaited_once_with(
                current, "b" * 32, reschedule
            )
            current.response.defer.assert_awaited_once_with(
                ephemeral=True, thinking=True
            )
        finally:
            view.stop()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "values",
    [
        {"user_id": 400},
        {"guild_id": 999},
        {"channel_id": 999},
    ],
)
def test_wrong_applicant_or_location_is_rejected(values):
    async def scenario():
        authorize = AsyncMock(return_value=True)
        handler = AsyncMock()
        view = NoShowView(
            record=record(),
            authorize=authorize,
            on_answer=handler,
        )
        current = interaction(**values)
        try:
            await view.children[0].callback(current)
            authorize.assert_not_awaited()
            handler.assert_not_awaited()
            current.edit_original_response.assert_awaited_once()
        finally:
            view.stop()

    asyncio.run(scenario())


def test_latest_authorization_can_reject_answer():
    async def scenario():
        authorize = AsyncMock(return_value=False)
        handler = AsyncMock()
        view = NoShowView(
            record=record(),
            authorize=authorize,
            on_answer=handler,
        )
        try:
            await view.children[0].callback(interaction())
            authorize.assert_awaited_once()
            handler.assert_not_awaited()
        finally:
            view.stop()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "user_id,authorized,expected",
    [
        (300, True, False),
        (400, False, False),
        (400, True, True),
    ],
)
def test_second_confirmation_requires_manager_authorization(
    user_id, authorized, expected
):
    async def scenario():
        authorize = AsyncMock(return_value=authorized)
        handler = AsyncMock()
        view = NoShowView(
            record=record("second_confirmation"),
            authorize=authorize,
            on_confirm=handler,
        )
        current = interaction(user_id=user_id)
        try:
            await view.children[0].callback(current)
            if expected:
                handler.assert_awaited_once_with(current, "b" * 32)
            else:
                handler.assert_not_awaited()
            if user_id == 300:
                authorize.assert_not_awaited()
        finally:
            view.stop()

    asyncio.run(scenario())


def test_button_ids_survive_recreation():
    async def scenario():
        first = NoShowView(
            record=record(),
            authorize=AsyncMock(),
            on_answer=AsyncMock(),
        )
        second = NoShowView(
            record=record(),
            authorize=AsyncMock(),
            on_answer=AsyncMock(),
        )
        try:
            first_ids = [item.custom_id for item in first.children]
            second_ids = [item.custom_id for item in second.children]
            assert first_ids == second_ids
            assert len(set(first_ids)) == 2
            assert first.is_persistent()
        finally:
            first.stop()
            second.stop()

    asyncio.run(scenario())


def test_message_uses_saved_deadline_and_requires_kick_approval():
    current = record()
    text = no_show_message(current)

    assert "<@300>" in text
    assert "再調整は1回のみ" in text
    assert "管理者へキックの承認を依頼" in text
    assert text == no_show_message(dict(current))


def test_completed_confirmation_cannot_show_answer_buttons():
    async def scenario():
        with pytest.raises(ValueError):
            NoShowView(
                record=record("reschedule_requested"),
                authorize=AsyncMock(),
                on_answer=AsyncMock(),
            )

    asyncio.run(scenario())
