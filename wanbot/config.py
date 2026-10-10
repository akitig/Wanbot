"""環境変数の読み込みと、Discord接続前の設定検証。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAX_DISCORD_ID = (1 << 64) - 1

FEATURE_NAMES = frozenset({
    "welcome",
    "valorant_diagnosis",
    "bump",
    "recruitment",
    "reaction_roles",
    "leave_log",
})

ID_FIELDS = (
    "WELCOME_CATEGORY_ID",
    "MEETING_CATEGORY_ID",
    "LOG_CATEGORY_ID",
    "MEMBER_ROLE_ID",
    "GACHI_ROLE_ID",
    "ENJOY_ROLE_ID",
    "RULES_CHANNEL_ID",
    "RULES_MESSAGE_ID",
    "KICK_APPROVAL_CHANNEL_ID",
    "VALO_RECRUIT_CHANNEL_ID",
    "BUMP_CHANNEL_ID",
    "LEAVE_LOG_CHANNEL_ID",
)

REQUIRED_IDS = {
    "welcome": (
        "WELCOME_CATEGORY_ID",
        "MEETING_CATEGORY_ID",
        "LOG_CATEGORY_ID",
        "MEMBER_ROLE_ID",
        "GACHI_ROLE_ID",
        "ENJOY_ROLE_ID",
        "RULES_CHANNEL_ID",
        "RULES_MESSAGE_ID",
        "KICK_APPROVAL_CHANNEL_ID",
    ),
    "recruitment": (
        "VALO_RECRUIT_CHANNEL_ID",
        "GACHI_ROLE_ID",
        "ENJOY_ROLE_ID",
    ),
    "bump": ("BUMP_CHANNEL_ID",),
    "leave_log": ("LEAVE_LOG_CHANNEL_ID",),
}


class ConfigError(ValueError):
    """設定の不備。実際の設定値をエラー文へ含めない。"""


@dataclass(frozen=True)
class GuildConfig:
    environment: str
    guild_id: int
    enabled_features: frozenset[str]
    admin_role_ids: frozenset[int]
    ids: Mapping[str, int]
    rules_emoji: str
    reaction_role_message_ids: frozenset[int]
    valorant_timeout_seconds: int = 1800

    def feature_enabled(self, feature: str) -> bool:
        return feature in self.enabled_features


@dataclass(frozen=True)
class AppConfig:
    discord_token: str = field(repr=False)
    log_level: str
    runtime_data_dir: Path
    guilds: Mapping[int, GuildConfig]
    dm_forward_enabled: bool
    dm_forward_user_id: int | None

    def guild_for(self, guild_id: int | None) -> GuildConfig | None:
        if guild_id is None:
            return None
        return self.guilds.get(guild_id)


def _text(values: Mapping[str, str], key: str) -> str:
    return values.get(key, "").strip()


def _id_value(raw: str, key: str) -> int:
    if not raw.isascii() or not raw.isdecimal():
        raise ConfigError(f"{key}: 正の整数IDを指定してください")
    # 巨大な文字列を整数変換する前に拒否する。
    if len(raw) > 20:
        raise ConfigError(f"{key}: IDの範囲が不正です")
    value = int(raw)
    if not 0 < value <= MAX_DISCORD_ID:
        raise ConfigError(f"{key}: IDの範囲が不正です")
    return value


def _optional_id(values: Mapping[str, str], key: str) -> int | None:
    raw = _text(values, key)
    return _id_value(raw, key) if raw else None


def _id_list(values: Mapping[str, str], key: str) -> frozenset[int]:
    raw = _text(values, key)
    if not raw:
        return frozenset()
    result: set[int] = set()
    for part in raw.split(","):
        value = _id_value(part.strip(), key)
        if value in result:
            raise ConfigError(f"{key}: 同じIDが重複しています")
        result.add(value)
    return frozenset(result)


def _boolean(values: Mapping[str, str], key: str) -> bool:
    raw = _text(values, key).lower()
    if raw in ("", "false"):
        return False
    if raw == "true":
        return True
    raise ConfigError(f"{key}: true または false を指定してください")


def _guild(values: Mapping[str, str], prefix: str) -> GuildConfig | None:
    feature_key = f"{prefix}_ENABLED_FEATURES"
    raw_features = _text(values, feature_key)
    features = frozenset(
        part.strip() for part in raw_features.split(",")
    ) if raw_features else frozenset()

    if not features <= FEATURE_NAMES:
        raise ConfigError(f"{feature_key}: 未対応の機能名が含まれています")

    guild_id = _optional_id(values, f"{prefix}_GUILD_ID")
    if guild_id is None:
        if features:
            raise ConfigError(f"{prefix}_GUILD_ID: 機能を有効にする場合は必須です")
        return None

    ids: dict[str, int] = {}
    for name in ID_FIELDS:
        value = _optional_id(values, f"{prefix}_{name}")
        if value is not None:
            ids[name] = value

    admins = _id_list(values, f"{prefix}_ADMIN_ROLE_IDS")
    rules_emoji = _text(values, f"{prefix}_RULES_EMOJI")
    reaction_messages = _id_list(values, f"{prefix}_REACTION_ROLE_MESSAGE_IDS")

    for feature in sorted(features):
        for name in REQUIRED_IDS.get(feature, ()):
            if name not in ids:
                raise ConfigError(f"{prefix}_{name}: {feature} に必要です")

    if features & {"welcome", "valorant_diagnosis"} and not admins:
        raise ConfigError(f"{prefix}_ADMIN_ROLE_IDS: 管理者ロールが必要です")

    if "welcome" in features:
        if "valorant_diagnosis" not in features:
            raise ConfigError(
                f"{feature_key}: welcomeにはvalorant_diagnosisも必要です"
            )
        if not rules_emoji:
            raise ConfigError(f"{prefix}_RULES_EMOJI: welcomeに必要です")

    if "reaction_roles" in features and not reaction_messages:
        raise ConfigError(
            f"{prefix}_REACTION_ROLE_MESSAGE_IDS: reaction_rolesに必要です"
        )

    gachi = ids.get("GACHI_ROLE_ID")
    enjoy = ids.get("ENJOY_ROLE_ID")
    if gachi is not None and gachi == enjoy:
        raise ConfigError(
            f"{prefix}_GACHI_ROLE_ID / {prefix}_ENJOY_ROLE_ID: 別のロールが必要です"
        )

    valorant_timeout_seconds = 1800
    if "valorant_diagnosis" in features:
        timeout_key = f"{prefix}_VALO_DIAGNOSIS_TIMEOUT_SECONDS"
        raw_timeout = _text(values, timeout_key)
        if raw_timeout:
            if (
                not raw_timeout.isascii()
                or not raw_timeout.isdecimal()
                or len(raw_timeout) > 10
            ):
                raise ConfigError(f"{timeout_key}: 正の整数の秒数を指定してください")
            valorant_timeout_seconds = int(raw_timeout)
            if not 1 <= valorant_timeout_seconds <= 2_147_483_647:
                raise ConfigError(f"{timeout_key}: 秒数が有効範囲外です")

    return GuildConfig(
        environment=prefix.lower(),
        guild_id=guild_id,
        enabled_features=features,
        admin_role_ids=admins,
        ids=MappingProxyType(ids),
        rules_emoji=rules_emoji,
        reaction_role_message_ids=reaction_messages,
        valorant_timeout_seconds=valorant_timeout_seconds,
    )


def parse_config(
    values: Mapping[str, str],
    *,
    base_dir: Path = PROJECT_ROOT,
) -> AppConfig:
    """渡された値を検証する。ファイルやDiscordにはアクセスしない。"""
    token = _text(values, "DISCORD_TOKEN")
    if not token:
        raise ConfigError("DISCORD_TOKEN: 設定が必要です")

    level = _text(values, "LOG_LEVEL").upper() or "INFO"
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ConfigError("LOG_LEVEL: 対応するログレベルを指定してください")

    guilds: dict[int, GuildConfig] = {}
    for prefix in ("PROD", "TEST"):
        guild = _guild(values, prefix)
        if guild is None:
            continue
        if guild.guild_id in guilds:
            raise ConfigError("PROD_GUILD_ID / TEST_GUILD_ID: 重複しています")
        guilds[guild.guild_id] = guild

    if not guilds:
        raise ConfigError("PROD_GUILD_ID または TEST_GUILD_IDを設定してください")

    dm_enabled = _boolean(values, "DM_FORWARD_ENABLED")
    dm_user_id = _optional_id(values, "DM_FORWARD_USER_ID")
    if dm_enabled and dm_user_id is None:
        raise ConfigError("DM_FORWARD_USER_ID: DM転送を有効にする場合は必須です")

    raw_path = _text(values, "RUNTIME_DATA_DIR") or "./runtime"
    runtime_path = Path(raw_path).expanduser()
    if not runtime_path.is_absolute():
        runtime_path = base_dir / runtime_path

    return AppConfig(
        discord_token=token,
        log_level=level,
        runtime_data_dir=runtime_path.resolve(),
        guilds=MappingProxyType(guilds),
        dm_forward_enabled=dm_enabled,
        dm_forward_user_id=dm_user_id,
    )


def load_config(
    env_file: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> AppConfig:
    """設定ファイルを読み、OSの環境変数を優先して適用する。"""
    path = (
        Path(env_file).expanduser().resolve()
        if env_file is not None
        else PROJECT_ROOT / ".env"
    )

    values: dict[str, str] = {}
    if path.is_file():
        values.update({
            key: value
            for key, value in dotenv_values(path, interpolate=False).items()
            if value is not None
        })

    values.update(os.environ if environ is None else environ)
    return parse_config(values, base_dir=path.parent)
