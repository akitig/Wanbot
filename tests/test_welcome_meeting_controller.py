import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wanbot.permissions import AccessDenied
from wanbot.welcome.meeting_controller import MeetingInterviewController
from wanbot.welcome.meeting_plan import MeetingPlanConflict


def controller():
    # 操作の境界を個別に検証するため、通信部分を置き換える。
    current = object.__new__(MeetingInterviewController)
    current._context = SimpleNamespace(
        guild_id=100, channel_id=500, user_id=300
    )
    current._session = AsyncMock()
    current._member = AsyncMock(return_value=SimpleNamespace(id=200))
    return current


@pytest.mark.parametrize(
    ("guild_id", "channel_id"),
    ((999, 500), (100, 999), (None, 500)),
)
def test_actor_rejects_other_location(guild_id, channel_id):
    async def scenario():
        current = controller()
        interaction = SimpleNamespace(
            guild_id=guild_id,
            channel_id=channel_id,
            user=SimpleNamespace(id=200),
        )
        with pytest.raises(AccessDenied):
            await current._actor(interaction)
        current._member.assert_not_awaited()

    asyncio.run(scenario())


def test_only_selected_owner_can_operate():
    async def scenario():
        current = controller()
        interaction = SimpleNamespace(
            guild_id=100, channel_id=500, user=SimpleNamespace(id=201)
        )
        with pytest.raises(AccessDenied):
            await current._actor(interaction, owner_id=200)
        current._member.assert_not_awaited()

    asyncio.run(scenario())


def test_actor_checks_current_member_roles():
    async def scenario():
        current = controller()
        interaction = SimpleNamespace(
            guild_id=100, channel_id=500, user=SimpleNamespace(id=200)
        )
        await current._actor(interaction, owner_id=200)
        current._member.assert_awaited_once_with(200)

    asyncio.run(scenario())


@pytest.mark.parametrize("status", ("waiting_second", "confirmed"))
def test_selection_cannot_replace_pending_or_confirmed_plan(status):
    async def scenario():
        current = controller()
        current.load_request = AsyncMock(return_value=object())
        current._plan = AsyncMock(
            return_value=SimpleNamespace(proposal_id="a", status=status)
        )
        with pytest.raises(MeetingPlanConflict):
            await current._selectable("a")

    asyncio.run(scenario())


def test_selection_rejects_stale_proposal():
    async def scenario():
        current = controller()
        current.load_request = AsyncMock(return_value=object())
        current._plan = AsyncMock(
            return_value=SimpleNamespace(proposal_id="new", status="declined")
        )
        with pytest.raises(MeetingPlanConflict):
            await current._selectable("old")

    asyncio.run(scenario())


def test_declined_proposal_allows_new_selection():
    async def scenario():
        current = controller()
        request = object()
        current.load_request = AsyncMock(return_value=request)
        current._plan = AsyncMock(
            return_value=SimpleNamespace(proposal_id="a", status="declined")
        )
        assert await current._selectable("a") is request

    asyncio.run(scenario())
