"""Discordから取得したサーバー情報と設定IDを照合する。"""

from __future__ import annotations

import discord

from wanbot.config import GuildConfig


CATEGORY_FIELDS = (
    "WELCOME_CATEGORY_ID",
    "MEETING_CATEGORY_ID",
    "LOG_CATEGORY_ID",
)

TEXT_CHANNEL_FIELDS = (
    "RULES_CHANNEL_ID",
    "KICK_APPROVAL_CHANNEL_ID",
    "VALO_RECRUIT_CHANNEL_ID",
    "BUMP_CHANNEL_ID",
    "LEAVE_LOG_CHANNEL_ID",
)

ROLE_FIELDS = (
    "MEMBER_ROLE_ID",
    "GACHI_ROLE_ID",
    "ENJOY_ROLE_ID",
)


def validate_guild_resources(
    config: GuildConfig,
    guild: discord.Guild,
) -> tuple[str, ...]:
    """問題のある設定項目名を返す。Discordへの変更は行わない。"""
    if guild.id != config.guild_id:
        return ("GUILD_ID",)

    if guild.unavailable:
        return ("GUILD_UNAVAILABLE",)

    errors: list[str] = []

    def valid_role(role_id: int) -> bool:
        role = guild.get_role(role_id)
        return (
            role is not None
            and role.guild.id == config.guild_id
            and not role.is_default()
        )

    for role_id in config.admin_role_ids:
        if not valid_role(role_id):
            errors.append("ADMIN_ROLE_IDS")
            break

    for field in ROLE_FIELDS:
        role_id = config.ids.get(field)
        if role_id is not None and not valid_role(role_id):
            errors.append(field)

    groups = (
        (CATEGORY_FIELDS, discord.CategoryChannel),
        (TEXT_CHANNEL_FIELDS, discord.TextChannel),
    )
    for fields, expected_type in groups:
        for field in fields:
            channel_id = config.ids.get(field)
            if channel_id is None:
                continue
            channel = guild.get_channel(channel_id)
            if (
                not isinstance(channel, expected_type)
                or channel.guild.id != config.guild_id
            ):
                errors.append(field)

    return tuple(errors)
