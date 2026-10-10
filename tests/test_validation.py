from unittest.mock import Mock

import discord

from wanbot.config import parse_config
from wanbot.validation import validate_guild_resources


def make_config(**overrides):
    values = {
        "DISCORD_TOKEN": "fake-token",
        "TEST_GUILD_ID": "200",
    }
    values.update(overrides)
    return parse_config(values).guild_for(200)


def make_guild():
    guild = Mock(spec=discord.Guild)
    guild.id = 200
    guild.unavailable = False
    guild.get_role.return_value = None
    guild.get_channel.return_value = None
    return guild


def make_role(guild, *, default=False):
    role = Mock(spec=discord.Role)
    role.guild = guild
    role.is_default.return_value = default
    return role


def test_empty_optional_settings_are_allowed():
    assert validate_guild_resources(make_config(), make_guild()) == ()


def test_wrong_guild_is_rejected():
    guild = make_guild()
    guild.id = 999

    assert validate_guild_resources(make_config(), guild) == ("GUILD_ID",)


def test_unavailable_guild_is_rejected():
    guild = make_guild()
    guild.unavailable = True

    assert validate_guild_resources(make_config(), guild) == ("GUILD_UNAVAILABLE",)


def test_existing_admin_role_is_accepted():
    config = make_config(TEST_ADMIN_ROLE_IDS="201")
    guild = make_guild()
    guild.get_role.return_value = make_role(guild)

    assert validate_guild_resources(config, guild) == ()


def test_missing_admin_role_is_rejected():
    config = make_config(TEST_ADMIN_ROLE_IDS="201")

    assert validate_guild_resources(config, make_guild()) == ("ADMIN_ROLE_IDS",)


def test_everyone_cannot_be_admin_role():
    config = make_config(TEST_ADMIN_ROLE_IDS="200")
    guild = make_guild()
    guild.get_role.return_value = make_role(guild, default=True)

    assert validate_guild_resources(config, guild) == ("ADMIN_ROLE_IDS",)


def test_text_channel_cannot_be_used_as_category():
    config = make_config(TEST_WELCOME_CATEGORY_ID="210")
    guild = make_guild()
    channel = Mock(spec=discord.TextChannel)
    channel.guild = guild
    guild.get_channel.return_value = channel

    assert validate_guild_resources(config, guild) == ("WELCOME_CATEGORY_ID",)


def test_category_is_accepted():
    config = make_config(TEST_WELCOME_CATEGORY_ID="210")
    guild = make_guild()
    channel = Mock(spec=discord.CategoryChannel)
    channel.guild = guild
    guild.get_channel.return_value = channel

    assert validate_guild_resources(config, guild) == ()


def test_channel_from_other_guild_is_rejected():
    config = make_config(TEST_BUMP_CHANNEL_ID="210")
    guild = make_guild()
    other_guild = make_guild()
    other_guild.id = 999
    channel = Mock(spec=discord.TextChannel)
    channel.guild = other_guild
    guild.get_channel.return_value = channel

    assert validate_guild_resources(config, guild) == ("BUMP_CHANNEL_ID",)


def test_missing_member_role_is_rejected():
    config = make_config(TEST_MEMBER_ROLE_ID="220")

    assert validate_guild_resources(config, make_guild()) == ("MEMBER_ROLE_ID",)
