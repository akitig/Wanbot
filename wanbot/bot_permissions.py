"""有効な機能に必要なBot権限を確認する。権限の変更は行わない。"""

from __future__ import annotations

import discord

from wanbot.config import GuildConfig


GUILD_REQUIREMENTS = {
    "welcome": ("manage_channels", "manage_roles", "kick_members"),
    "reaction_roles": ("manage_roles",),
    "leave_log": ("view_audit_log",),
}

CHANNEL_REQUIREMENTS = {
    "welcome": {
        "WELCOME_CATEGORY_ID": ("view_channel", "manage_channels", "manage_roles"),
        "MEETING_CATEGORY_ID": ("view_channel", "manage_channels", "manage_roles"),
        "LOG_CATEGORY_ID": ("view_channel", "manage_channels", "manage_roles"),
        "RULES_CHANNEL_ID": ("view_channel", "read_message_history"),
        "KICK_APPROVAL_CHANNEL_ID": (
            "view_channel",
            "send_messages",
            "embed_links",
            "read_message_history",
        ),
    },
    "bump": {
        "BUMP_CHANNEL_ID": (
            "view_channel",
            "send_messages",
            "embed_links",
            "read_message_history",
        ),
    },
    "recruitment": {
        "VALO_RECRUIT_CHANNEL_ID": (
            "view_channel",
            "send_messages",
            "embed_links",
        ),
    },
    "leave_log": {
        "LEAVE_LOG_CHANNEL_ID": (
            "view_channel",
            "send_messages",
            "embed_links",
            "read_message_history",
        ),
    },
}

WELCOME_ASSIGNABLE_ROLES = (
    "MEMBER_ROLE_ID",
    "GACHI_ROLE_ID",
    "ENJOY_ROLE_ID",
)


def validate_bot_permissions(
    config: GuildConfig,
    guild: discord.Guild,
) -> tuple[str, ...]:
    """不足を固定の識別名で返す。ログへ設定値や本文を渡さない。"""
    if guild.id != config.guild_id:
        return ("guild_mismatch",)
    if guild.unavailable:
        return ("guild_unavailable",)
    if not config.enabled_features:
        return ()

    member = guild.me
    if member is None:
        return ("bot_member_unavailable",)

    errors: list[str] = []

    for feature in sorted(config.enabled_features):
        for permission in GUILD_REQUIREMENTS.get(feature, ()):
            if not getattr(member.guild_permissions, permission):
                errors.append(f"{feature}_guild_{permission}")

        for field, permissions in CHANNEL_REQUIREMENTS.get(feature, {}).items():
            channel_id = config.ids.get(field)
            channel = guild.get_channel(channel_id) if channel_id is not None else None

            if (
                channel is None
                or not isinstance(channel, discord.abc.GuildChannel)
                or channel.guild.id != config.guild_id
            ):
                errors.append(f"{feature}_{field.lower()}_unavailable")
                continue

            effective = channel.permissions_for(member)
            for permission in permissions:
                if not getattr(effective, permission):
                    errors.append(f"{feature}_{field.lower()}_{permission}")

    if "welcome" in config.enabled_features:
        for field in WELCOME_ASSIGNABLE_ROLES:
            role_id = config.ids.get(field)
            role = guild.get_role(role_id) if role_id is not None else None

            if role is None or role.guild.id != config.guild_id:
                errors.append(f"{field.lower()}_unavailable")
            elif (
                role.is_default()
                or role.managed
                or not role < member.top_role
            ):
                errors.append(f"{field.lower()}_not_assignable")

    return tuple(errors)
