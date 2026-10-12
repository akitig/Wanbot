from unittest.mock import Mock

import discord
import pytest

from wanbot.welcome.channels import (
    WelcomeChannelPermissionsError,
    permission_signature,
)


def member(member_id):
    target = Mock(spec=discord.Member)
    target.id = member_id
    return target


def role(role_id):
    target = Mock(spec=discord.Role)
    target.id = role_id
    return target


def test_uncached_member_has_same_permissions():
    expected = {
        member(300): discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
        ),
    }
    actual = {
        discord.Object(id=300, type=discord.User): discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
        ),
    }

    assert permission_signature(actual) == permission_signature(expected)


def test_uncached_role_has_same_permissions():
    expected = {
        role(400): discord.PermissionOverwrite(view_channel=True),
    }
    actual = {
        discord.Object(id=400, type=discord.Role): discord.PermissionOverwrite(
            view_channel=True
        ),
    }

    assert permission_signature(actual) == permission_signature(expected)


def test_extra_member_permission_is_detected():
    expected = {
        member(300): discord.PermissionOverwrite(view_channel=True),
    }
    actual = {
        member(300): discord.PermissionOverwrite(view_channel=True),
        member(999): discord.PermissionOverwrite(view_channel=True),
    }

    assert permission_signature(actual) != permission_signature(expected)


def test_missing_everyone_denial_is_detected():
    expected = {
        role(100): discord.PermissionOverwrite(view_channel=False),
    }
    actual = {
        role(100): discord.PermissionOverwrite(),
    }

    assert permission_signature(actual) != permission_signature(expected)


def test_role_and_member_with_same_id_are_distinguished():
    expected = {
        role(400): discord.PermissionOverwrite(view_channel=True),
    }
    actual = {
        member(400): discord.PermissionOverwrite(view_channel=True),
    }

    assert permission_signature(actual) != permission_signature(expected)


def test_unknown_object_type_is_rejected():
    actual = {
        discord.Object(id=300): discord.PermissionOverwrite(view_channel=True),
    }

    with pytest.raises(WelcomeChannelPermissionsError):
        permission_signature(actual)
