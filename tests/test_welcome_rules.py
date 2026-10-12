import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from wanbot.config import GuildConfig
from wanbot.welcome.rules import (
    RulesCheckError,
    RulesReactionChecker,
    emoji_key,
)


class FakeReaction:
    def __init__(self, emoji="✅", normal=(), burst=()):
        self.emoji = emoji
        self.normal = tuple(normal)
        self.burst = tuple(burst)
        self.burst_count = len(self.burst)
        self.error = None

    async def users(self, *, limit, type):
        assert limit is None
        if self.error is not None:
            raise self.error
        ids = (
            self.burst
            if type == discord.ReactionType.burst
            else self.normal
        )
        for user_id in ids:
            yield SimpleNamespace(id=user_id)


def make_case(reactions=(), emoji="✅"):
    config = GuildConfig(
        environment="test",
        guild_id=100,
        enabled_features=frozenset({"welcome"}),
        admin_role_ids=frozenset({400}),
        ids={"RULES_CHANNEL_ID": 500, "RULES_MESSAGE_ID": 600},
        rules_emoji=emoji,
        reaction_role_message_ids=frozenset(),
    )
    guild = SimpleNamespace(id=100)
    channel = Mock(spec=discord.TextChannel)
    channel.id = 500
    channel.guild = guild

    message = SimpleNamespace(
        id=600,
        channel=channel,
        guild=guild,
        reactions=list(reactions),
    )
    channel.fetch_message = AsyncMock(return_value=message)
    client = SimpleNamespace(
        fetch_channel=AsyncMock(return_value=channel),
    )
    checker = RulesReactionChecker(client=client, guild_config=config)
    return checker, client, channel, message


@pytest.mark.parametrize(
    ("reaction", "expected"),
    [
        (FakeReaction(normal=(300,)), True),
        (FakeReaction(normal=(301,)), False),
        (FakeReaction("❌", normal=(300,)), False),
        (FakeReaction(burst=(300,)), True),
        (FakeReaction(normal=range(1, 301)), True),
    ],
)
def test_only_target_user_and_emoji_are_accepted(reaction, expected):
    checker, client, channel, _ = make_case([reaction])
    assert asyncio.run(checker.has_reacted(300)) is expected
    client.fetch_channel.assert_awaited_once_with(500)
    channel.fetch_message.assert_awaited_once_with(600)


def test_no_reactions_is_false():
    checker, _, _, _ = make_case()
    assert asyncio.run(checker.has_reacted(300)) is False


def test_custom_emoji_matches_id_after_rename():
    reaction = FakeReaction(
        discord.PartialEmoji(name="new_name", id=900),
        normal=(300,),
    )
    checker, _, _, _ = make_case([reaction], "<:old_name:900>")
    assert asyncio.run(checker.has_reacted(300)) is True


def test_same_custom_emoji_name_with_different_id_is_rejected():
    reaction = FakeReaction(
        discord.PartialEmoji(name="approved", id=901),
        normal=(300,),
    )
    checker, _, _, _ = make_case([reaction], "<:approved:900>")
    assert asyncio.run(checker.has_reacted(300)) is False


def test_unicode_presentation_is_normalized():
    assert emoji_key("☑") == emoji_key("☑️")


def test_removed_reaction_is_not_cached():
    async def scenario():
        checker, client, _, message = make_case(
            [FakeReaction(normal=(300,))]
        )
        assert await checker.has_reacted(300) is True
        message.reactions = []
        assert await checker.has_reacted(300) is False
        assert client.fetch_channel.await_count == 2

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "target",
    ["channel_guild", "channel_id", "message_id", "message_guild"],
)
def test_wrong_destination_is_rejected(target):
    checker, _, channel, message = make_case(
        [FakeReaction(normal=(300,))]
    )
    if target == "channel_guild":
        channel.guild = SimpleNamespace(id=999)
    elif target == "channel_id":
        channel.id = 999
    elif target == "message_id":
        message.id = 999
    else:
        message.guild = SimpleNamespace(id=999)

    with pytest.raises(RulesCheckError):
        asyncio.run(checker.has_reacted(300))


@pytest.mark.parametrize("stage", ["channel", "message", "users"])
def test_discord_failure_is_not_treated_as_confirmation(stage):
    reaction = FakeReaction(normal=(300,))
    checker, client, channel, _ = make_case([reaction])
    error = discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"),
        {"code": 50013, "message": "Missing Permissions"},
    )
    if stage == "channel":
        client.fetch_channel.side_effect = error
    elif stage == "message":
        channel.fetch_message.side_effect = error
    else:
        reaction.error = error

    with pytest.raises(discord.Forbidden):
        asyncio.run(checker.has_reacted(300))


def test_message_url_points_to_configured_rules():
    checker, _, _, _ = make_case()
    assert checker.message_url == (
        "https://discord.com/channels/100/500/600"
    )
