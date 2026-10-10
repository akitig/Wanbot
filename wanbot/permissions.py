"""Discord接続なしで確認できる、共通の権限判定。"""

from __future__ import annotations

from collections.abc import Iterable

from wanbot.config import AppConfig, FEATURE_NAMES, GuildConfig


class AccessDenied(PermissionError):
    """サーバー・機能・権限の条件を満たしていない。"""


def is_registered_guild(config: AppConfig, guild_id: int | None) -> bool:
    return config.guild_for(guild_id) is not None


def is_feature_enabled(
    config: AppConfig,
    guild_id: int | None,
    feature: str,
) -> bool:
    guild = config.guild_for(guild_id)
    return (
        feature in FEATURE_NAMES
        and guild is not None
        and guild.feature_enabled(feature)
    )


def is_manager(
    config: AppConfig,
    guild_id: int | None,
    member_role_ids: Iterable[int],
) -> bool:
    guild = config.guild_for(guild_id)
    if guild is None:
        return False
    return not guild.admin_role_ids.isdisjoint(member_role_ids)


def require_feature(
    config: AppConfig,
    guild_id: int | None,
    feature: str,
) -> GuildConfig:
    """対象サーバーで機能が有効なら、そのサーバーの設定を返す。"""
    guild = config.guild_for(guild_id)
    if guild is None:
        raise AccessDenied("このサーバーでは利用できません。")
    if feature not in FEATURE_NAMES or not guild.feature_enabled(feature):
        raise AccessDenied("このサーバーでは、この機能は無効です。")
    return guild


def require_manager(
    config: AppConfig,
    guild_id: int | None,
    member_role_ids: Iterable[int],
    *,
    feature: str,
) -> GuildConfig:
    """機能が有効で、指定管理者ロールも持っている場合だけ許可する。"""
    guild = require_feature(config, guild_id, feature)
    if not is_manager(config, guild_id, member_role_ids):
        raise AccessDenied("この操作は指定された管理者のみ実行できます。")
    return guild


def require_same_guild(
    expected_guild_id: int,
    actual_guild_id: int | None,
) -> None:
    """取得したチャンネル・ロールなどの所属サーバーを確認する。"""
    if actual_guild_id != expected_guild_id:
        raise AccessDenied("対象が設定されたサーバーに所属していません。")
