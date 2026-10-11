import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from wanbot.config import parse_config
from wanbot.recruitment.models import make_draft
from wanbot.recruitment.publisher import (
    RecruitmentPublisher,
    RecruitmentPublishError,
)


def setup_publisher():
    config = parse_config({
        "DISCORD_TOKEN": "test-token",
        "TEST_GUILD_ID": "100",
        "TEST_ENABLED_FEATURES": "recruitment",
        "TEST_VALO_RECRUIT_CHANNEL_ID": "200",
        "TEST_GACHI_ROLE_ID": "400",
        "TEST_ENJOY_ROLE_ID": "500",
    })

    guild = SimpleNamespace(
        id=100,
        unavailable=False,
        me=SimpleNamespace(id=600),
    )
    roles = {
        role_id: SimpleNamespace(
            id=role_id,
            guild=guild,
            mentionable=True,
        )
        for role_id in (400, 500)
    }
    guild.get_role = Mock(side_effect=roles.get)

    permissions = SimpleNamespace(
        view_channel=True,
        send_messages=True,
        embed_links=True,
        mention_everyone=False,
    )

    channel = Mock(spec=discord.TextChannel)
    channel.id = 200
    channel.guild = guild
    channel.permissions_for = Mock(return_value=permissions)
    channel.send = AsyncMock()

    client = Mock(spec=discord.Client)
    client.get_guild.return_value = guild
    client.get_channel.return_value = channel

    available = Mock(return_value=True)

    publisher = RecruitmentPublisher(
        client=client,
        guild=config.guild_for(100),
        is_available=available,
    )

    interaction = SimpleNamespace(
        guild_id=100,
        channel_id=200,
        # ロール情報を持たなくても募集できる。
        user=SimpleNamespace(id=300, bot=False),
    )

    return SimpleNamespace(
        publisher=publisher,
        interaction=interaction,
        client=client,
        guild=guild,
        channel=channel,
        roles=roles,
        permissions=permissions,
        available=available,
    )


def make_recruitment(targets=frozenset({"enjoy"})):
    return make_draft(
        targets=targets,
        modes="アンレート",
        player_count="あと2人",
        comment="@everyone <@&999> 気軽にどうぞ",
    )


@pytest.mark.parametrize(
    ("targets", "expected_ids"),
    [
        (frozenset({"gachi"}), [400]),
        (frozenset({"enjoy"}), [500]),
        (frozenset({"gachi", "enjoy"}), [400, 500]),
    ],
)
def test_selected_roles_are_sent_to_configured_channel(
    targets,
    expected_ids,
):
    async def run():
        item = setup_publisher()

        await item.publisher.publish(
            item.interaction,
            make_recruitment(targets),
        )

        item.channel.send.assert_awaited_once()
        kwargs = item.channel.send.call_args.kwargs

        assert kwargs["content"] == " ".join(
            f"<@&{role_id}>" for role_id in expected_ids
        )

        mentions = kwargs["allowed_mentions"]
        assert [role.id for role in mentions.roles] == expected_ids
        assert mentions.everyone is False
        assert mentions.users is False
        assert mentions.replied_user is False

    asyncio.run(run())


@pytest.mark.parametrize("field", ["guild_id", "channel_id"])
def test_other_scope_cannot_publish(field):
    async def run():
        item = setup_publisher()
        setattr(item.interaction, field, 999)

        with pytest.raises(RecruitmentPublishError):
            await item.publisher.publish(
                item.interaction,
                make_recruitment(),
            )

        item.channel.send.assert_not_awaited()

    asyncio.run(run())


def test_unavailable_feature_cannot_publish():
    async def run():
        item = setup_publisher()
        item.available.return_value = False

        with pytest.raises(RecruitmentPublishError):
            await item.publisher.publish(
                item.interaction,
                make_recruitment(),
            )

        item.channel.send.assert_not_awaited()

    asyncio.run(run())


def test_missing_channel_cannot_publish():
    async def run():
        item = setup_publisher()
        item.client.get_channel.return_value = None

        with pytest.raises(RecruitmentPublishError):
            await item.publisher.publish(
                item.interaction,
                make_recruitment(),
            )

        item.channel.send.assert_not_awaited()

    asyncio.run(run())


def test_deleted_target_role_cannot_publish():
    async def run():
        item = setup_publisher()
        del item.roles[500]

        with pytest.raises(RecruitmentPublishError):
            await item.publisher.publish(
                item.interaction,
                make_recruitment(),
            )

        item.channel.send.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize("mention_permission", [False, True])
def test_nonmentionable_role_requires_bot_permission(mention_permission):
    async def run():
        item = setup_publisher()
        item.roles[500].mentionable = False
        item.permissions.mention_everyone = mention_permission

        if mention_permission:
            await item.publisher.publish(
                item.interaction,
                make_recruitment(),
            )
            item.channel.send.assert_awaited_once()
        else:
            with pytest.raises(RecruitmentPublishError):
                await item.publisher.publish(
                    item.interaction,
                    make_recruitment(),
                )
            item.channel.send.assert_not_awaited()

    asyncio.run(run())


def test_send_failure_is_not_retried():
    async def run():
        item = setup_publisher()
        item.channel.send.side_effect = RuntimeError("Send failed")

        with pytest.raises(RuntimeError, match="Send failed"):
            await item.publisher.publish(
                item.interaction,
                make_recruitment(),
            )

        item.channel.send.assert_awaited_once()

    asyncio.run(run())


def test_lost_send_permission_prevents_posting():
    async def run():
        item = setup_publisher()
        item.permissions.send_messages = False

        with pytest.raises(RecruitmentPublishError):
            await item.publisher.publish(
                item.interaction,
                make_recruitment(),
            )

        item.channel.send.assert_not_awaited()

    asyncio.run(run())
