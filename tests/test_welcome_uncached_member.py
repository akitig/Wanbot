from unittest.mock import Mock

import discord

from wanbot.welcome.channels import permission_signature


def test_abc_user_object_matches_member():
    member = Mock(spec=discord.Member)
    member.id = 300
    overwrite = discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
    )

    actual = {
        discord.Object(id=300, type=discord.abc.User): overwrite,
    }
    expected = {member: overwrite}

    assert permission_signature(actual) == permission_signature(expected)


def test_discord_channel_overwrites_without_cached_member():
    guild = Mock(spec=discord.Guild)
    guild.get_member.return_value = None

    channel = discord.TextChannel.__new__(discord.TextChannel)
    channel.guild = guild
    channel._fill_overwrites({
        "permission_overwrites": [
            {
                "id": "300",
                "type": 1,
                "allow": str(discord.Permissions(view_channel=True).value),
                "deny": "0",
            }
        ]
    })

    member = Mock(spec=discord.Member)
    member.id = 300
    expected = {
        member: discord.PermissionOverwrite(view_channel=True),
    }

    assert permission_signature(
        channel.overwrites
    ) == permission_signature(expected)


def test_abc_user_permission_difference_is_not_ignored():
    target = discord.Object(id=300, type=discord.abc.User)

    allowed = {
        target: discord.PermissionOverwrite(view_channel=True),
    }
    denied = {
        target: discord.PermissionOverwrite(view_channel=False),
    }

    assert permission_signature(allowed) != permission_signature(denied)
