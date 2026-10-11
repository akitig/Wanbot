from __future__ import annotations

import asyncio

import discord

from wanbot.config import GuildConfig
from wanbot.welcome.repository import WelcomeRepository, WelcomeSession
from wanbot.welcome.naming import welcome_channel_name


class WelcomeChannelError(RuntimeError):
    """Welcomeチャンネルの作成・復旧条件を満たしていない。"""


class WelcomeChannelIdentityError(WelcomeChannelError):
    """チャンネルの所属・カテゴリ・識別子が不一致。"""


class WelcomeChannelPermissionsError(WelcomeChannelError):
    """チャンネルの権限が不一致。"""


def permission_signature(overwrites):
    result = {}

    for target, overwrite in overwrites.items():
        if isinstance(target, discord.Role):
            kind = "role"
        elif isinstance(target, (discord.Member, discord.User)):
            kind = "member"
        elif isinstance(target, discord.Object):
            if target.type is discord.Role:
                kind = "role"
            elif target.type in (
                discord.Member,
                discord.User,
                discord.abc.User,
            ):
                kind = "member"
            else:
                raise WelcomeChannelPermissionsError(
                    "Unknown overwrite target type"
                )
        else:
            raise WelcomeChannelPermissionsError(
                "Unknown overwrite target"
            )

        key = (kind, target.id)
        if key in result:
            raise WelcomeChannelPermissionsError(
                "Duplicate overwrite target"
            )

        allow, deny = overwrite.pair()
        result[key] = (allow.value, deny.value)

    return result


class WelcomeChannels:
    def __init__(
        self,
        *,
        client: discord.Client,
        guild_config: GuildConfig,
        repository: WelcomeRepository,
    ) -> None:
        self._client = client
        self._config = guild_config
        self._repository = repository
        # 1サーバーにつき1インスタンスを使い、同時作成を直列化する。
        self._lock = asyncio.Lock()

    def _targets(self, member: discord.Member):
        config = self._config

        if (
            not config.feature_enabled("welcome")
            or member.guild.id != config.guild_id
            or member.bot
        ):
            raise WelcomeChannelError("Invalid Welcome member")

        guild = self._client.get_guild(config.guild_id)
        if guild is None or guild.unavailable or guild.me is None:
            raise WelcomeChannelError("Welcome guild is unavailable")

        category = guild.get_channel(config.ids["WELCOME_CATEGORY_ID"])
        if (
            not isinstance(category, discord.CategoryChannel)
            or category.guild.id != config.guild_id
        ):
            raise WelcomeChannelError("Invalid Welcome category")

        if not config.admin_role_ids:
            raise WelcomeChannelError("Welcome managers are missing")

        admins = []
        for role_id in config.admin_role_ids:
            role = guild.get_role(role_id)
            if (
                role is None
                or role.guild.id != config.guild_id
                or role.is_default()
            ):
                raise WelcomeChannelError("Invalid Welcome manager role")
            admins.append(role)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=False,
            ),
            member: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                embed_links=True,
                manage_channels=True,
            ),
        }
        for role in admins:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            )

        return guild, category, overwrites

    def _topic(self, session: WelcomeSession) -> str:
        return (
            f"wanbot:welcome:{self._config.guild_id}:"
            f"{session.session_id}"
        )

    def _check_channel(self, channel, session, category, overwrites):
        if (
            not isinstance(channel, discord.TextChannel)
            or channel.guild.id != self._config.guild_id
            or channel.category_id != category.id
            or channel.topic != self._topic(session)
        ):
            raise WelcomeChannelIdentityError(
                "Welcome channel identity does not match"
            )

        actual = permission_signature(channel.overwrites)
        expected = permission_signature(overwrites)

        if actual != expected:
            raise WelcomeChannelPermissionsError(
                "Welcome channel permissions do not match"
            )

    async def ensure(self, member: discord.Member) -> WelcomeSession:
        async with self._lock:
            guild, category, overwrites = self._targets(member)
            session = await self._repository.begin(member.id)

            if session.state == "active":
                # 保存済みチャンネルを確認する。見つからなくても、
                # 勝手に新しい案内へ置き換えない。
                channel = await self._client.fetch_channel(
                    session.channel_id
                )
                self._check_channel(
                    channel, session, category, overwrites
                )
                return session

            # 作成直後に保存が失敗した場合も復旧できるよう、
            # Discordから最新のチャンネル一覧を取得する。
            channels = await guild.fetch_channels()
            matches = [
                channel
                for channel in channels
                if getattr(channel, "topic", None) == self._topic(session)
            ]

            if len(matches) > 1:
                raise WelcomeChannelError("Duplicate Welcome channels")

            if matches:
                channel = matches[0]
            else:
                channel = await guild.create_text_channel(
                    name=welcome_channel_name(member),
                    category=category,
                    topic=self._topic(session),
                    overwrites=overwrites,
                    reason="Wanbot Welcome onboarding",
                )

            self._check_channel(channel, session, category, overwrites)

            return await self._repository.attach_channel(
                session.session_id,
                channel.id,
            )
