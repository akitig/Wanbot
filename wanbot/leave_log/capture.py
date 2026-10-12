"""退出イベントのユーザー情報と、取得できたメンバー情報を記録する。"""

from wanbot.leave_log.models import (
    DepartureDataError,
    DepartureSnapshot,
    RoleSnapshot,
)


def capture_departure(
    *, event_id, guild_id, user, member, observed_at
):
    """退出イベントをまとめる呼出元から、共通のevent_idを受け取る。"""
    display_name = None
    roles = None
    joined_at = None

    if member is not None:
        if member.id != user.id or member.guild.id != guild_id:
            raise DepartureDataError(
                "退出したユーザーとメンバー情報の対象が一致しません。"
            )

        display_name = member.display_name
        joined_at = member.joined_at
        member_roles = getattr(member, "roles", None)
        if member_roles is not None:
            roles = tuple(
                RoleSnapshot(role_id=role.id, name=role.name)
                for role in member_roles
                if role.id != guild_id
            )

    return DepartureSnapshot(
        event_id=event_id,
        guild_id=guild_id,
        user_id=user.id,
        username=user.name,
        display_name=display_name,
        roles=roles,
        joined_at=joined_at,
        observed_at=observed_at,
    )
