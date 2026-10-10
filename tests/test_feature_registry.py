import asyncio

import pytest

from wanbot.config import parse_config
from wanbot.features.registry import FeatureManager, FeatureRegistry


def make_config(*, production_enabled=False):
    values = {
        "DISCORD_TOKEN": "fake-token",
        "PROD_GUILD_ID": "100",
        "TEST_GUILD_ID": "200",
        "TEST_ENABLED_FEATURES": "bump",
        "TEST_BUMP_CHANNEL_ID": "210",
    }
    if production_enabled:
        values.update({
            "PROD_ENABLED_FEATURES": "bump",
            "PROD_BUMP_CHANNEL_ID": "110",
        })
    return parse_config(values)


class FakeFeature:
    def __init__(self, guild_id, events, *, fail_start=False, fail_stop=False):
        self.guild_id = guild_id
        self.events = events
        self.fail_start = fail_start
        self.fail_stop = fail_stop

    async def start(self):
        self.events.append(("start", self.guild_id))
        if self.fail_start:
            raise RuntimeError("simulated startup failure")

    async def stop(self):
        self.events.append(("stop", self.guild_id))
        if self.fail_stop:
            raise RuntimeError("simulated shutdown failure")


def test_test_only_feature_does_not_start_in_production():
    async def scenario():
        events = []
        registry = FeatureRegistry()
        registry.register("bump", lambda guild: FakeFeature(guild.guild_id, events))
        manager = FeatureManager(make_config(), registry)

        await manager.start()

        assert events == [("start", 200)]
        assert manager.is_active(200, "bump")
        assert not manager.is_active(100, "bump")
        assert not manager.is_active(999, "bump")
        assert not manager.is_active(None, "bump")

        await manager.stop()
        assert events == [("start", 200), ("stop", 200)]
        assert not manager.is_active(200, "bump")

    asyncio.run(scenario())


def test_start_and_stop_are_not_duplicated():
    async def scenario():
        events = []
        registry = FeatureRegistry()
        registry.register("bump", lambda guild: FakeFeature(guild.guild_id, events))
        manager = FeatureManager(make_config(), registry)

        await manager.start()
        await manager.start()
        await manager.stop()
        await manager.stop()
        await manager.start()

        assert events == [("start", 200), ("stop", 200)]

    asyncio.run(scenario())


def test_missing_implementation_is_not_active():
    async def scenario():
        manager = FeatureManager(make_config(), FeatureRegistry())

        await manager.start()

        assert not manager.is_active(200, "bump")
        await manager.stop()

    asyncio.run(scenario())


def test_failed_feature_is_cleaned_up_and_other_guild_continues():
    async def scenario():
        events = []
        registry = FeatureRegistry()
        registry.register(
            "bump",
            lambda guild: FakeFeature(
                guild.guild_id,
                events,
                fail_start=guild.guild_id == 100,
            ),
        )
        manager = FeatureManager(make_config(production_enabled=True), registry)

        await manager.start()

        assert not manager.is_active(100, "bump")
        assert manager.is_active(200, "bump")
        assert ("stop", 100) in events

        await manager.stop()
        assert ("stop", 200) in events

    asyncio.run(scenario())


def test_shutdown_failure_does_not_skip_other_features():
    async def scenario():
        events = []
        registry = FeatureRegistry()
        registry.register(
            "bump",
            lambda guild: FakeFeature(
                guild.guild_id,
                events,
                fail_stop=guild.guild_id == 200,
            ),
        )
        manager = FeatureManager(make_config(production_enabled=True), registry)

        await manager.start()
        await manager.stop()

        assert ("stop", 200) in events
        assert ("stop", 100) in events
        assert not manager.is_active(100, "bump")
        assert not manager.is_active(200, "bump")

    asyncio.run(scenario())


def test_unknown_feature_cannot_be_registered():
    registry = FeatureRegistry()

    with pytest.raises(ValueError):
        registry.register("unknown", lambda guild: FakeFeature(guild.guild_id, []))


def test_duplicate_registration_is_rejected():
    registry = FeatureRegistry()
    def factory(guild):
        return FakeFeature(guild.guild_id, [])

    registry.register("bump", factory)

    with pytest.raises(ValueError):
        registry.register("bump", factory)
