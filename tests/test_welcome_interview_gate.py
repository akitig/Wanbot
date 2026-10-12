import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wanbot.valorant.session import SessionStatus
from wanbot.welcome.interview_gate import InterviewGate, InterviewNotReady


def make_case(*, plays_valo=True):
    context = SimpleNamespace(
        guild_id=100,
        channel_id=500,
        user_id=300,
        session_id="a" * 32,
        is_available=lambda: True,
    )
    welcome = SimpleNamespace(
        get_active=AsyncMock(
            return_value=SimpleNamespace(
                state="active",
                session_id=context.session_id,
                user_id=300,
                channel_id=500,
            )
        )
    )
    questions = SimpleNamespace(
        get=AsyncMock(
            return_value=SimpleNamespace(
                session_id=context.session_id,
                user_id=300,
                progress=SimpleNamespace(
                    status="completed",
                    plays_valo=plays_valo,
                ),
            )
        )
    )
    rules = SimpleNamespace(has_reacted=AsyncMock(return_value=True))
    diagnosis = AsyncMock(
        return_value=SimpleNamespace(
            session=SimpleNamespace(
                guild_id=100,
                user_id=300,
                status=SessionStatus.COMPLETED,
            ),
            result=object(),
        )
    )
    gate = InterviewGate(
        context=context,
        welcome_repository=welcome,
        questionnaire_repository=questions,
        rules_checker=rules,
        load_diagnosis=diagnosis,
    )
    return SimpleNamespace(
        gate=gate,
        context=context,
        welcome=welcome,
        questions=questions,
        rules=rules,
        diagnosis=diagnosis,
    )


def test_all_conditions_allow_progress():
    case = make_case()
    asyncio.run(case.gate.check())
    case.diagnosis.assert_awaited_once()
    case.rules.has_reacted.assert_awaited_once_with(300)


def test_non_valorant_member_does_not_need_diagnosis():
    case = make_case(plays_valo=False)
    asyncio.run(case.gate.check())
    case.diagnosis.assert_not_awaited()
    case.rules.has_reacted.assert_awaited_once_with(300)


@pytest.mark.parametrize(
    "reason",
    [
        "closed",
        "different_welcome",
        "questions_incomplete",
        "diagnosis_missing",
        "diagnosis_active",
        "diagnosis_other_user",
        "diagnosis_other_guild",
        "result_missing",
        "reaction_missing",
    ],
)
def test_unmet_condition_blocks_progress(reason):
    case = make_case()

    if reason == "closed":
        case.welcome.get_active.return_value.state = "closed"
    elif reason == "different_welcome":
        case.welcome.get_active.return_value.session_id = "b" * 32
    elif reason == "questions_incomplete":
        case.questions.get.return_value.progress.status = "answering"
    elif reason == "diagnosis_missing":
        case.diagnosis.return_value = None
    elif reason == "diagnosis_active":
        case.diagnosis.return_value.session.status = SessionStatus.ACTIVE
    elif reason == "diagnosis_other_user":
        case.diagnosis.return_value.session.user_id = 301
    elif reason == "diagnosis_other_guild":
        case.diagnosis.return_value.session.guild_id = 101
    elif reason == "result_missing":
        case.diagnosis.return_value.result = None
    else:
        case.rules.has_reacted.return_value = False

    with pytest.raises(InterviewNotReady):
        asyncio.run(case.gate.check())


def test_removed_reaction_is_checked_again():
    async def scenario():
        case = make_case()
        await case.gate.check()
        case.rules.has_reacted.return_value = False
        with pytest.raises(InterviewNotReady):
            await case.gate.check()
        assert case.rules.has_reacted.await_count == 2

    asyncio.run(scenario())


def test_reaction_lookup_failure_does_not_allow_progress():
    case = make_case()
    case.rules.has_reacted.side_effect = RuntimeError("unavailable")
    with pytest.raises(RuntimeError):
        asyncio.run(case.gate.check())


def test_shutdown_during_reaction_lookup_blocks_progress():
    async def scenario():
        case = make_case()

        async def lookup(user_id):
            case.context.is_available = lambda: False
            return True

        case.rules.has_reacted.side_effect = lookup
        with pytest.raises(InterviewNotReady):
            await case.gate.check()

    asyncio.run(scenario())
