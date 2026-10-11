"""業務機能を動かさず、Discord接続と参加サーバーを確認する。"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Iterable

import discord

from wanbot.bot_permissions import validate_bot_permissions
from wanbot.config import AppConfig, ConfigError, load_config
from wanbot.logging_config import configure_logging, log_event
from wanbot.validation import validate_guild_resources


def validate_connection_config(config: AppConfig) -> None:
    """この確認用起動では、業務機能を有効にしない。"""
    if any(guild.enabled_features for guild in config.guilds.values()):
        raise ConfigError(
            "接続確認ではPROD_ENABLED_FEATURESと"
            "TEST_ENABLED_FEATURESを空欄にしてください。"
        )
    if config.dm_forward_enabled:
        raise ConfigError(
            "接続確認ではDM_FORWARD_ENABLED=falseにしてください。"
        )


def missing_guild_ids(
    config: AppConfig,
    connected_guild_ids: Iterable[int],
) -> frozenset[int]:
    return frozenset(config.guilds).difference(connected_guild_ids)


class ConnectionCheckClient(discord.Client):
    def __init__(self, config: AppConfig) -> None:
        intents = discord.Intents.none()
        intents.guilds = True

        # BUMP成功の本文・埋め込みと、投稿の更新を受け取る。
        # DMメッセージやメンバー一覧の購読はここでは有効にしない。
        if any(
            guild.feature_enabled("bump")
            for guild in config.guilds.values()
        ):
            intents.guild_messages = True
            intents.message_content = True
        super().__init__(intents=intents)

        self.config = config
        self.connection_check_failed = False

    async def on_ready(self) -> None:
        missing = missing_guild_ids(
            self.config,
            (guild.id for guild in self.guilds),
        )

        if missing:
            self.connection_check_failed = True
            for guild_id in sorted(missing):
                log_event(
                    "lifecycle",
                    "configured_guild_unavailable",
                    guild_id=guild_id,
                    level=logging.ERROR,
                )
            # 対象を確認できない場合は、この確認用起動を終了する。
            await self.close()
            return

        validation_failed = False

        for guild_id, guild_config in self.config.guilds.items():
            guild = self.get_guild(guild_id)
            if guild is None:
                validation_failed = True
                log_event(
                    "validation",
                    "guild_unavailable",
                    guild_id=guild_id,
                    level=logging.ERROR,
                )
                continue

            errors = validate_guild_resources(guild_config, guild)
            if not errors:
                errors = validate_bot_permissions(guild_config, guild)
            if errors:
                validation_failed = True
                for field in errors:
                    log_event(
                        "validation",
                        f"invalid_{field.lower()}",
                        guild_id=guild_id,
                        level=logging.ERROR,
                    )
                continue

            log_event(
                "lifecycle",
                "guild_resources_verified",
                guild_id=guild_id,
            )

        if validation_failed:
            self.connection_check_failed = True
            await self.close()
            return

        log_event("lifecycle", "connection_ready")

    async def on_disconnect(self) -> None:
        log_event("lifecycle", "disconnected")

    async def on_error(self, event_method: str, *args, **kwargs) -> None:
        # イベント引数には個人情報が含まれ得るため出力しない。
        log_event(
            "lifecycle",
            "event_failed",
            level=logging.ERROR,
            error=sys.exc_info()[1],
        )
        self.connection_check_failed = True
        await self.close()


async def run_connection_check(config: AppConfig) -> int:
    async with ConnectionCheckClient(config) as client:
        await client.start(config.discord_token)
        return 1 if client.connection_check_failed else 0


def main() -> int:
    try:
        config = load_config()
        validate_connection_config(config)
    except ConfigError as error:
        print(f"設定エラー: {error}", file=sys.stderr)
        return 1
    except (OSError, UnicodeError):
        print(
            "設定ファイルを読み込めません。パス・権限・文字コードを確認してください。",
            file=sys.stderr,
        )
        return 1

    configure_logging(
        config.log_level,
        secrets=(config.discord_token,),
    )
    log_event("lifecycle", "connection_starting")

    try:
        return asyncio.run(run_connection_check(config))
    except KeyboardInterrupt:
        log_event("lifecycle", "stop_requested")
        return 0
    except discord.LoginFailure:
        log_event(
            "lifecycle",
            "login_failed",
            level=logging.ERROR,
        )
        return 1
    except Exception as error:
        log_event(
            "lifecycle",
            "connection_failed",
            level=logging.ERROR,
            error=error,
        )
        return 1
    finally:
        log_event("lifecycle", "connection_stopped")


if __name__ == "__main__":
    raise SystemExit(main())
