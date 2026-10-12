"""Welcomeチャンネルを運営専用のログへ移す。"""

import asyncio

import discord

from wanbot.config import GuildConfig
from wanbot.welcome.channels import (
    WelcomeChannelError,
    WelcomeChannelIdentityError,
    WelcomeChannelPermissionsError,
    permission_signature,
)
from wanbot.welcome.repository import WelcomeSession


class WelcomeArchiveChannel:
    def __init__(
        self,
        *,
        client: discord.Client,
        guild_config: GuildConfig,
    ) -> None:
        self._client = client
        self._config = guild_config
        self._lock = asyncio.Lock()

    def _targets(self):
        config = self._config
        if not config.feature_enabled("welcome"):
            raise WelcomeChannelError("Welcome is disabled")

        guild = self._client.get_guild(config.guild_id)
        if (
            guild is None
            or guild.id != config.guild_id
            or guild.unavailable
            or guild.me is None
        ):
            raise WelcomeChannelError("Welcome guild is unavailable")

        log_id = config.ids.get("LOG_CATEGORY_ID")
        welcome_id = config.ids.get("WELCOME_CATEGORY_ID")
        category = guild.get_channel(log_id)

        if (
            log_id == welcome_id
            or not isinstance(category, discord.CategoryChannel)
            or category.id != log_id
            or category.guild.id != config.guild_id
        ):
            raise WelcomeChannelError("Invalid Welcome log category")

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=False,
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                embed_links=True,
                manage_channels=True,
            ),
        }

        if not config.admin_role_ids:
            raise WelcomeChannelError("Welcome managers are missing")

        for role_id in config.admin_role_ids:
            role = guild.get_role(role_id)
            if (
                role is None
                or role.id != role_id
                or role.guild.id != config.guild_id
                or role.is_default()
            ):
                raise WelcomeChannelError("Invalid Welcome manager role")
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            )

        return guild, category, overwrites

    def _topic(self, session):
        return (
            f"wanbot:welcome:{self._config.guild_id}:"
            f"{session.session_id}"
        )

    def _check_identity(self, channel, session):
        if (
            not isinstance(channel, discord.TextChannel)
            or channel.guild.id != self._config.guild_id
            or channel.topic != self._topic(session)
            or (
                session.channel_id is not None
                and channel.id != session.channel_id
            )
            or channel.category_id not in (
                self._config.ids["WELCOME_CATEGORY_ID"],
                self._config.ids["LOG_CATEGORY_ID"],
            )
        ):
            raise WelcomeChannelIdentityError(
                "Welcome archive channel identity does not match"
            )

    async def archive(self, session: WelcomeSession) -> int | None:
        """移動を確認したチャンネルIDを返す。保存データは変更しない。"""
        if session.state != "archiving":
            raise WelcomeChannelError("Welcome archive is not pending")

        async with self._lock:
            guild, category, overwrites = self._targets()

            if session.channel_id is not None:
                # 削除・取得失敗時はエラーを返し、終了扱いにしない。
                channel = await self._client.fetch_channel(
                    session.channel_id
                )
            else:
                # 作成後、IDの保存前に停止した場合もtopicから探す。
                channels = await guild.fetch_channels()
                matches = [
                    item
                    for item in channels
                    if getattr(item, "topic", None) == self._topic(session)
                ]
                if len(matches) > 1:
                    raise WelcomeChannelError(
                        "Duplicate Welcome archive channels"
                    )
                if not matches:
                    return None
                channel = matches[0]

            self._check_identity(channel, session)
            expected = permission_signature(overwrites)

            if (
                channel.category_id != category.id
                or permission_signature(channel.overwrites) != expected
            ):
                await channel.edit(
                    category=category,
                    sync_permissions=False,
                    overwrites=overwrites,
                    reason="Wanbot Welcome archive",
                )

            # キャッシュではなく、Discordから取得し直して確認する。
            confirmed = await self._client.fetch_channel(channel.id)
            self._check_identity(confirmed, session)

            if confirmed.id != channel.id:
                raise WelcomeChannelIdentityError(
                    "Welcome archive channel ID does not match"
                )
            if confirmed.category_id != category.id:
                raise WelcomeChannelIdentityError(
                    "Welcome archive category does not match"
                )
            if permission_signature(confirmed.overwrites) != expected:
                raise WelcomeChannelPermissionsError(
                    "Welcome archive permissions do not match"
                )

            return confirmed.id
