"""本人期限の設定と、本番での短縮防止。"""

from types import SimpleNamespace

import pytest

from wanbot.config import ConfigError, REQUIRED_IDS, parse_config
from wanbot.welcome.waiting_limits import (
    DEFAULT_LIMITS,
    WaitingLimits,
    duration_text,
    limits_for_guild,
)


def config_values(prefix="TEST"):
    values = {
        "DISCORD_TOKEN": "unit-test-token",
        f"{prefix}_GUILD_ID": "100",
        f"{prefix}_ENABLED_FEATURES": "welcome,valorant_diagnosis",
        f"{prefix}_ADMIN_ROLE_IDS": "200",
        f"{prefix}_RULES_EMOJI": "👍",
    }
    for number, name in enumerate(REQUIRED_IDS["welcome"], start=1000):
        values[f"{prefix}_{name}"] = str(number)
    return values


def test_default_deadlines_are_48_and_72_hours():
    config = parse_config(config_values())
    guild = config.guild_for(100)
    assert limits_for_guild(guild) == DEFAULT_LIMITS


def test_test_guild_accepts_two_and_three_minutes():
    values = config_values()
    values["TEST_WELCOME_REMINDER_SECONDS"] = "120"
    values["TEST_WELCOME_TIMEOUT_SECONDS"] = "180"

    guild = parse_config(values).guild_for(100)

    assert limits_for_guild(guild) == WaitingLimits(120, 180)


def test_production_rejects_shortened_deadlines():
    values = config_values("PROD")
    values["PROD_WELCOME_REMINDER_SECONDS"] = "120"
    values["PROD_WELCOME_TIMEOUT_SECONDS"] = "180"

    with pytest.raises(ConfigError):
        parse_config(values)


def test_production_accepts_explicit_standard_deadlines():
    values = config_values("PROD")
    values["PROD_WELCOME_REMINDER_SECONDS"] = "172800"
    values["PROD_WELCOME_TIMEOUT_SECONDS"] = "259200"

    guild = parse_config(values).guild_for(100)
    assert limits_for_guild(guild) == DEFAULT_LIMITS


@pytest.mark.parametrize(
    "reminder,timeout",
    [
        ("0", "180"),
        ("-1", "180"),
        ("120.5", "180"),
        ("abc", "180"),
        ("120", "120"),
        ("180", "120"),
        ("120", "2147483648"),
    ],
)
def test_invalid_deadline_settings_are_rejected(reminder, timeout):
    values = config_values()
    values["TEST_WELCOME_REMINDER_SECONDS"] = reminder
    values["TEST_WELCOME_TIMEOUT_SECONDS"] = timeout

    with pytest.raises(ConfigError):
        parse_config(values)


def test_disabled_welcome_ignores_unused_deadline_values():
    values = {
        "DISCORD_TOKEN": "unit-test-token",
        "TEST_GUILD_ID": "100",
        "TEST_WELCOME_REMINDER_SECONDS": "invalid",
        "TEST_WELCOME_TIMEOUT_SECONDS": "invalid",
    }
    guild = parse_config(values).guild_for(100)
    assert limits_for_guild(guild) == DEFAULT_LIMITS


def test_production_guard_also_rejects_direct_short_configuration():
    guild = SimpleNamespace(
        environment="prod",
        welcome_reminder_seconds=120,
        welcome_timeout_seconds=180,
    )
    with pytest.raises(ValueError):
        limits_for_guild(guild)


def test_policy_fingerprint_changes_when_deadlines_change():
    assert WaitingLimits(120, 180).fingerprint != DEFAULT_LIMITS.fingerprint


@pytest.mark.parametrize(
    "seconds,text",
    [(120, "2分"), (180, "3分"), (172800, "48時間"), (90, "90秒")],
)
def test_duration_display(seconds, text):
    assert duration_text(seconds) == text
