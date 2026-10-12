"""面談の日程・担当者の決定と、2人目の承諾を管理する。"""

from dataclasses import dataclass, replace
from datetime import datetime
from uuid import uuid4

from wanbot.permissions import AccessDenied, require_manager


class MeetingPlanConflict(ValueError):
    """現在の面談状態では受け付けられない操作。"""


@dataclass(frozen=True)
class MeetingPlan:
    proposal_id: str
    welcome_session_id: str
    guild_id: int
    user_id: int
    channel_id: int
    preference: int
    meeting_at: datetime
    first_manager_id: int
    second_manager_id: int | None
    status: str
    proposed_at: datetime
    decided_at: datetime | None


def _id(value):
    if type(value) is not int or not 0 < value < 2**64:
        raise ValueError("Invalid Discord ID")


def _time(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Time must include a timezone")


def propose_meeting(
    *,
    config,
    guild_id,
    request,
    actor_id,
    actor_role_ids,
    preference,
    second_manager_id,
    second_manager_role_ids,
    now,
    previous=None,
):
    """運営が、希望日時と担当者を選んで決定する。"""
    require_manager(
        config, guild_id, actor_role_ids, feature="welcome"
    )
    _id(actor_id)
    _id(request.user_id)
    _id(request.channel_id)
    _time(now)

    if actor_id == request.user_id:
        raise AccessDenied("面談の対象者自身を担当者にはできません。")

    if previous is not None:
        if (
            previous.welcome_session_id != request.session_id
            or previous.guild_id != guild_id
            or previous.user_id != request.user_id
            or previous.channel_id != request.channel_id
        ):
            raise MeetingPlanConflict("面談の対象が一致しません。")
        if previous.status != "declined":
            raise MeetingPlanConflict(
                "すでに担当者の返事待ち、または面談日時が確定しています。"
            )

    if type(preference) is not int or preference not in (1, 2, 3):
        raise MeetingPlanConflict("第一希望から第三希望のいずれかを選んでください。")

    meeting_at = request.preferences[preference - 1]
    _time(meeting_at)
    if meeting_at <= now:
        raise MeetingPlanConflict(
            "選択した希望日時は過ぎています。日程の再調整が必要です。"
        )

    if second_manager_id is not None:
        _id(second_manager_id)
        if second_manager_id in (actor_id, request.user_id):
            raise MeetingPlanConflict(
                "2人目には、1人目の担当者・面談対象者以外を選んでください。"
            )
        require_manager(
            config,
            guild_id,
            second_manager_role_ids,
            feature="welcome",
        )

    confirmed = second_manager_id is None
    return MeetingPlan(
        proposal_id=uuid4().hex,
        welcome_session_id=request.session_id,
        guild_id=guild_id,
        user_id=request.user_id,
        channel_id=request.channel_id,
        preference=preference,
        meeting_at=meeting_at,
        first_manager_id=actor_id,
        second_manager_id=second_manager_id,
        status="confirmed" if confirmed else "waiting_second",
        proposed_at=now,
        decided_at=now if confirmed else None,
    )


def respond_to_meeting(
    plan,
    *,
    config,
    proposal_id,
    actor_id,
    actor_role_ids,
    accepted,
    now,
):
    """選ばれた2人目の運営だけが、承諾・辞退できる。"""
    require_manager(
        config, plan.guild_id, actor_role_ids, feature="welcome"
    )
    _id(actor_id)
    _time(now)

    if actor_id != plan.second_manager_id:
        raise AccessDenied("この確認に回答できるのは、選ばれた担当者本人だけです。")
    if proposal_id != plan.proposal_id:
        raise MeetingPlanConflict("古い確認ボタンです。最新の案内をご確認ください。")
    if plan.status != "waiting_second":
        raise MeetingPlanConflict("この確認はすでに回答済みです。")
    if type(accepted) is not bool:
        raise ValueError("Acceptance must be a boolean")
    if now < plan.proposed_at:
        raise MeetingPlanConflict("確認依頼より前の回答は受け付けられません。")
    if accepted and plan.meeting_at <= now:
        raise MeetingPlanConflict(
            "面談予定時刻を過ぎているため、確定できません。"
            "日程の再調整が必要です。"
        )

    return replace(
        plan,
        status="confirmed" if accepted else "declined",
        decided_at=now,
    )
