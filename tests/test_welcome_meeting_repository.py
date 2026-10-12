import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from wanbot.config import ID_FIELDS, parse_config
from wanbot.permissions import AccessDenied
from wanbot.welcome.meeting_plan import MeetingPlanConflict
from wanbot.welcome.meeting_repository import MeetingPlanRepository


NOW = datetime(2026, 10, 11, 7, 0, tzinfo=timezone.utc)


def make_case(tmp_path):
    values = {
        "DISCORD_TOKEN": "test-token",
        "TEST_GUILD_ID": "100",
        "TEST_ENABLED_FEATURES": "welcome,valorant_diagnosis",
        "TEST_ADMIN_ROLE_IDS": "400",
        "TEST_RULES_EMOJI": "👍",
        "RUNTIME_DATA_DIR": str(tmp_path / "runtime"),
    }
    values.update({
        f"TEST_{name}": str(1000 + index)
        for index, name in enumerate(ID_FIELDS)
    })
    config = parse_config(values, base_dir=tmp_path)
    request = SimpleNamespace(
        session_id="a" * 32,
        user_id=300,
        channel_id=500,
        preferences=tuple(
            NOW + timedelta(days=number) for number in (1, 2, 3)
        ),
    )
    return config, request


async def propose(repository, request, **overrides):
    arguments = {
        "request": request,
        "actor_id": 200,
        "actor_role_ids": [400],
        "preference": 1,
        "second_manager_id": 201,
        "second_manager_role_ids": [400],
        "now": NOW,
    }
    arguments.update(overrides)
    return await repository.propose(**arguments)


async def respond(repository, plan, **overrides):
    arguments = {
        "session_id": plan.welcome_session_id,
        "proposal_id": plan.proposal_id,
        "actor_id": 201,
        "actor_role_ids": [400],
        "accepted": True,
        "now": NOW + timedelta(minutes=1),
    }
    arguments.update(overrides)
    return await repository.respond(**arguments)


def test_waiting_plan_survives_reopening(tmp_path):
    async def scenario():
        config, request = make_case(tmp_path)
        repository = MeetingPlanRepository(config, 100)
        try:
            original = await propose(repository, request)
        finally:
            await repository.close()

        reopened = MeetingPlanRepository(config, 100)
        try:
            assert await reopened.get(request.session_id) == original
            confirmed = await respond(reopened, original)
            assert confirmed.status == "confirmed"
            assert await reopened.get(request.session_id) == confirmed
        finally:
            await reopened.close()

    asyncio.run(scenario())


def test_concurrent_proposals_accept_only_one(tmp_path):
    async def scenario():
        config, request = make_case(tmp_path)
        first = MeetingPlanRepository(config, 100)
        second = MeetingPlanRepository(config, 100)
        try:
            results = await asyncio.gather(
                propose(first, request),
                propose(second, request, actor_id=202),
                return_exceptions=True,
            )
            assert sum(
                isinstance(result, MeetingPlanConflict) for result in results
            ) == 1
            assert (await first.get(request.session_id)).status == "waiting_second"
        finally:
            await first.close()
            await second.close()

    asyncio.run(scenario())


def test_concurrent_responses_accept_only_one(tmp_path):
    async def scenario():
        config, request = make_case(tmp_path)
        repository = MeetingPlanRepository(config, 100)
        try:
            plan = await propose(repository, request)
            results = await asyncio.gather(
                respond(repository, plan),
                respond(repository, plan, accepted=False),
                return_exceptions=True,
            )
            assert sum(
                isinstance(result, MeetingPlanConflict) for result in results
            ) == 1
            assert (await repository.get(request.session_id)).status in {
                "confirmed", "declined"
            }
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_old_confirmation_cannot_answer_replacement(tmp_path):
    async def scenario():
        config, request = make_case(tmp_path)
        repository = MeetingPlanRepository(config, 100)
        try:
            original = await propose(repository, request)
            await respond(repository, original, accepted=False)
            replacement = await propose(
                repository,
                request,
                expected_proposal_id=original.proposal_id,
                second_manager_id=202,
            )
            with pytest.raises(MeetingPlanConflict):
                await respond(
                    repository, original, actor_id=202
                )
            assert await repository.get(request.session_id) == replacement
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_unauthorized_response_does_not_change_saved_plan(tmp_path):
    async def scenario():
        config, request = make_case(tmp_path)
        repository = MeetingPlanRepository(config, 100)
        try:
            plan = await propose(repository, request)
            with pytest.raises(AccessDenied):
                await respond(repository, plan, actor_role_ids=[])
            assert await repository.get(request.session_id) == plan
        finally:
            await repository.close()

    asyncio.run(scenario())
