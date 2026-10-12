import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wanbot.valorant.scoring import Category
from wanbot.valorant.session import SessionStatus
from wanbot.welcome.admission_roles import (
    admission_role_ids,
    grant_admission_roles,
)
from wanbot.welcome.interview_gate import InterviewNotReady


def inputs(plays_valo=True, category=Category.NEUTRAL):
    context = SimpleNamespace(
        guild_id=100, user_id=300, session_id="a" * 32
    )
    config = SimpleNamespace(
        guild_id=100,
        ids={
            "MEMBER_ROLE_ID": 10,
            "GACHI_ROLE_ID": 20,
            "ENJOY_ROLE_ID": 30,
        },
    )
    questionnaire = SimpleNamespace(
        session_id=context.session_id,
        user_id=300,
        progress=SimpleNamespace(
            status="completed",
            adult=True,
            plays_valo=plays_valo,
        ),
    )
    diagnosis = SimpleNamespace(
        session=SimpleNamespace(
            guild_id=100,
            user_id=300,
            status=SessionStatus.COMPLETED,
        ),
        result=SimpleNamespace(category=category),
    )
    return config, context, questionnaire, diagnosis


@pytest.mark.parametrize(
    ("category", "expected"),
    (
        (Category.GACHI, (10, 20)),
        (Category.ENJOY, (10, 30)),
        (Category.NEUTRAL, (10, 20, 30)),
    ),
)
def test_diagnosis_role_mapping(category, expected):
    config, context, questions, diagnosis = inputs(category=category)
    assert admission_role_ids(
        config,
        context=context,
        questionnaire=questions,
        diagnosis=diagnosis,
    ) == expected


def test_non_valorant_member_only_gets_member_role():
    config, context, questions, _ = inputs(plays_valo=False)
    assert admission_role_ids(
        config,
        context=context,
        questionnaire=questions,
        diagnosis=None,
    ) == (10,)


def test_missing_diagnosis_is_not_silently_skipped():
    config, context, questions, _ = inputs()
    with pytest.raises(InterviewNotReady):
        admission_role_ids(
            config,
            context=context,
            questionnaire=questions,
            diagnosis=None,
        )


def test_other_welcome_questionnaire_is_rejected():
    config, context, questions, diagnosis = inputs()
    questions.session_id = "b" * 32
    with pytest.raises(InterviewNotReady):
        admission_role_ids(
            config,
            context=context,
            questionnaire=questions,
            diagnosis=diagnosis,
        )


def test_other_members_diagnosis_is_rejected():
    config, context, questions, diagnosis = inputs()
    diagnosis.session.user_id = 999
    with pytest.raises(InterviewNotReady):
        admission_role_ids(
            config,
            context=context,
            questionnaire=questions,
            diagnosis=diagnosis,
        )


def test_unknown_category_is_rejected():
    config, context, questions, diagnosis = inputs(category="unknown")
    with pytest.raises(InterviewNotReady):
        admission_role_ids(
            config,
            context=context,
            questionnaire=questions,
            diagnosis=diagnosis,
        )


class FakeRole:
    def __init__(self, role_id, position, guild):
        self.id = role_id
        self.position = position
        self.guild = guild
        self.managed = False

    def is_default(self):
        return self.id == self.guild.id

    def __lt__(self, other):
        return self.position < other.position


def discord_case(*, apply_roles=True):
    guild = SimpleNamespace(id=100, unavailable=False)
    member_role = FakeRole(10, 1, guild)
    diagnosis_role = FakeRole(20, 2, guild)
    top_role = FakeRole(900, 10, guild)
    unrelated = FakeRole(40, 3, guild)
    member = SimpleNamespace(
        id=300, guild=guild, bot=False, roles=[unrelated]
    )
    bot = SimpleNamespace(
        id=999,
        guild=guild,
        roles=[top_role],
        guild_permissions=SimpleNamespace(manage_roles=True),
    )

    async def add(*roles, **kwargs):
        if apply_roles:
            member.roles.extend(roles)

    member.add_roles = AsyncMock(side_effect=add)
    guild.fetch_member = AsyncMock(
        side_effect=lambda user_id: bot if user_id == 999 else member
    )
    guild.fetch_roles = AsyncMock(
        return_value=[member_role, diagnosis_role, top_role, unrelated]
    )
    client = SimpleNamespace(
        user=SimpleNamespace(id=999),
        get_guild=lambda guild_id: guild if guild_id == 100 else None,
    )
    return client, member, diagnosis_role


def test_roles_are_added_once_without_removing_unrelated_roles():
    async def scenario():
        client, member, _ = discord_case()
        for _ in range(2):
            await grant_admission_roles(
                client=client, guild_id=100, user_id=300, role_ids=(10, 20)
            )
        assert {role.id for role in member.roles} == {10, 20, 40}
        member.add_roles.assert_awaited_once()

    asyncio.run(scenario())


def test_unassignable_role_stops_before_any_addition():
    async def scenario():
        client, member, role = discord_case()
        role.position = 20
        with pytest.raises(InterviewNotReady):
            await grant_admission_roles(
                client=client, guild_id=100, user_id=300, role_ids=(10, 20)
            )
        member.add_roles.assert_not_awaited()

    asyncio.run(scenario())


def test_missing_roles_after_api_call_is_failure():
    async def scenario():
        client, _, _ = discord_case(apply_roles=False)
        with pytest.raises(InterviewNotReady):
            await grant_admission_roles(
                client=client, guild_id=100, user_id=300, role_ids=(10, 20)
            )

    asyncio.run(scenario())
