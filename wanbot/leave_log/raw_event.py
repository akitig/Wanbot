"""raw_member_removeを、保存用の退出情報へ変換する。"""

import discord

from wanbot.leave_log.capture import capture_departure


def capture_raw_departure(payload, *, event_id, observed_at):
    user = payload.user
    member = user if isinstance(user, discord.Member) else None
    return capture_departure(
        event_id=event_id,
        guild_id=payload.guild_id,
        user=user,
        member=member,
        observed_at=observed_at,
    )
