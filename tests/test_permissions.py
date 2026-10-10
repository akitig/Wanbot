import pytest

from wanbot.config import parse_config
from wanbot.permissions import (
    AccessDenied,
    is_feature_enabled,
    is_manager,
    is_registered_guild,
    require_feature,
    require_manager,
    require_same_guild,
)


@pytest.fixture
def config():
    return parse_config({
        "DISCORD_TOKEN": "fake-token",
        "PROD_GUILD_ID": "100",
        "PROD_ADMIN_ROLE_IDS": "101",
        "TEST_GUILD_ID": "200",
        "TEST_ADMIN_ROLE_IDS": "201,202",
        "TEST_ENABLED_FEATURES": "bump",
        "TEST_BUMP_CHANNEL_ID": "210",
    })


def test_registered_guilds(config):
    assert is_registered_guild(config, 100)
    assert is_registered_guild(config, 200)
    assert not is_registered_guild(config, 999)
    assert not is_registered_guild(config, None)


def test_feature_is_test_only(config):
    assert is_feature_enabled(config, 200, "bump")
    assert not is_feature_enabled(config, 100, "bump")
    assert not is_feature_enabled(config, 999, "bump")
    assert not is_feature_enabled(config, None, "bump")
    assert not is_feature_enabled(config, 200, "unknown")


def test_any_configured_manager_role_is_accepted(config):
    assert is_manager(config, 200, [202, 888])


def test_other_guild_manager_role_is_not_accepted(config):
    assert not is_manager(config, 200, [101])
    assert not is_manager(config, 100, [201])


def test_no_manager_role(config):
    assert not is_manager(config, 200, [])
    assert not is_manager(config, 200, [888])
    assert not is_manager(config, None, [201])
    assert not is_manager(config, 999, [201])


def test_require_feature_returns_correct_guild(config):
    assert require_feature(config, 200, "bump").guild_id == 200


@pytest.mark.parametrize("guild_id", [None, 999, 100])
def test_require_feature_denies_unavailable_context(config, guild_id):
    with pytest.raises(AccessDenied):
        require_feature(config, guild_id, "bump")


def test_require_manager_allows_configured_role(config):
    guild = require_manager(config, 200, [201], feature="bump")
    assert guild.guild_id == 200


def test_require_manager_rejects_wrong_role(config):
    with pytest.raises(AccessDenied):
        require_manager(config, 200, [101], feature="bump")


def test_manager_cannot_use_disabled_feature(config):
    with pytest.raises(AccessDenied):
        require_manager(config, 100, [101], feature="bump")


def test_same_guild_is_accepted():
    require_same_guild(200, 200)


@pytest.mark.parametrize("actual", [100, None])
def test_wrong_resource_guild_is_rejected(actual):
    with pytest.raises(AccessDenied):
        require_same_guild(200, actual)
