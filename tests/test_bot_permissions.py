from unittest.mock import Mock

import discord

from wanbot.bot_permissions import validate_bot_permissions
from wanbot.config import parse_config


def make_config(*, enabled=True):
    values = {
        "DISCORD_TOKEN": "fake-token",
        "TEST_GUILD_ID": "200",
    }
    if enabled:
        values.update({
            "TEST_ENABLED_FEATURES": "bump",
            "TEST_DISBOARD_BOT_ID": "300",
            "TEST_BUMP_CHANNEL_ID": "210",
        })
    return parse_config(values).guild_for(200)


def make_guild():
    guild = Mock(spec=discord.Guild)
    guild.id = 200
    guild.unavailable = False

    member = Mock(spec=discord.Member)
    member.guild_permissions = discord.Permissions.none()
    guild.me = member

    channel = Mock(spec=discord.TextChannel)
    channel.guild = guild
    permissions = discord.Permissions.none()
    permissions.view_channel = True
    permissions.send_messages = True
    permissions.embed_links = True
    permissions.read_message_history = True
    channel.permissions_for.return_value = permissions

    guild.get_channel.return_value = channel
    return guild, channel


def test_disabled_features_need_no_permissions():
    guild, _ = make_guild()
    guild.me = None

    assert validate_bot_permissions(make_config(enabled=False), guild) == ()


def test_bump_permissions_are_accepted():
    guild, _ = make_guild()

    assert validate_bot_permissions(make_config(), guild) == ()


def test_missing_bot_member_is_rejected():
    guild, _ = make_guild()
    guild.me = None

    assert validate_bot_permissions(make_config(), guild) == (
        "bot_member_unavailable",
    )


def test_channel_send_permission_is_checked():
    guild, channel = make_guild()
    channel.permissions_for.return_value.send_messages = False

    errors = validate_bot_permissions(make_config(), guild)

    assert "bump_bump_channel_id_send_messages" in errors


def test_channel_permission_overrides_guild_permission():
    guild, channel = make_guild()
    guild.me.guild_permissions.send_messages = True
    channel.permissions_for.return_value.send_messages = False

    errors = validate_bot_permissions(make_config(), guild)

    assert "bump_bump_channel_id_send_messages" in errors


def test_missing_channel_is_rejected():
    guild, _ = make_guild()
    guild.get_channel.return_value = None

    assert validate_bot_permissions(make_config(), guild) == (
        "bump_bump_channel_id_unavailable",
    )


def test_other_guild_channel_is_rejected():
    guild, channel = make_guild()
    other_guild = Mock(spec=discord.Guild)
    other_guild.id = 999
    channel.guild = other_guild

    assert validate_bot_permissions(make_config(), guild) == (
        "bump_bump_channel_id_unavailable",
    )


def test_wrong_guild_is_rejected():
    guild, _ = make_guild()
    guild.id = 999

    assert validate_bot_permissions(make_config(), guild) == ("guild_mismatch",)


def test_unavailable_guild_is_rejected():
    guild, _ = make_guild()
    guild.unavailable = True

    assert validate_bot_permissions(make_config(), guild) == ("guild_unavailable",)
