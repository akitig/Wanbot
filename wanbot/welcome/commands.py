from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import discord
from discord import app_commands

from wanbot.config import AppConfig
from wanbot.logging_config import log_event
from wanbot.permissions import AccessDenied, require_manager
from wanbot.welcome.repository import WelcomeSession


CreateWelcome = Callable[
    [discord.Member],
    Awaitable[WelcomeSession],
]


async def _reply(
    interaction: discord.Interaction,
    content: str,
) -> None:
    kwargs = {
        "ephemeral": True,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if interaction.response.is_done():
        await interaction.followup.send(content, **kwargs)
    else:
        await interaction.response.send_message(content, **kwargs)


def register_welcome_command(
    tree: app_commands.CommandTree,
    config: AppConfig,
    guild_id: int,
    *,
    is_available: Callable[[], bool],
    create_welcome: CreateWelcome,
) -> None:
    guild = config.guild_for(guild_id)
    if guild is None or not guild.feature_enabled("welcome"):
        raise ValueError("Welcome is not enabled for this guild")

    def authorize(interaction: discord.Interaction) -> None:
        if (
            interaction.guild_id != guild_id
            or not isinstance(interaction.user, discord.Member)
        ):
            raise AccessDenied("このサーバーでは操作できません。")

        require_manager(
            config,
            guild_id,
            (role.id for role in interaction.user.roles),
            feature="welcome",
        )

        if not is_available():
            raise AccessDenied("現在、Welcome機能を利用できません。")

    @app_commands.command(
        name="welcome",
        description="指定したメンバーのWelcome案内を作成・確認します。",
    )
    @app_commands.guild_only()
    @app_commands.describe(member="案内するメンバー")
    async def welcome(
        interaction: discord.Interaction,
        member: discord.Member,
    ) -> None:
        try:
            authorize(interaction)

            if member.guild.id != guild_id:
                raise AccessDenied("同じサーバーのメンバーを指定してください。")
            if member.bot:
                raise AccessDenied("Botは案内の対象にできません。")
        except AccessDenied as error:
            await _reply(interaction, str(error))
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            # 応答待ちの間に権限を失った場合も、作成処理へ進めない。
            authorize(interaction)
            session = await create_welcome(member)

            if (
                session.user_id != member.id
                or session.state != "active"
                or type(session.channel_id) is not int
                or not 0 < session.channel_id < 1 << 64
            ):
                raise RuntimeError("Invalid Welcome creation result")
        except AccessDenied as error:
            await _reply(interaction, str(error))
            return
        except Exception as error:
            log_event(
                "welcome",
                "manual_creation_failed",
                guild_id=guild_id,
                level=logging.ERROR,
                error=error,
            )
            await _reply(
                interaction,
                "案内の作成・確認を完了できませんでした。\n"
                "チャンネルが作成済みの場合もあります。"
                "管理者はログを確認してください。",
            )
            return

        await _reply(
            interaction,
            f"Welcome案内先：<# {session.channel_id}>".replace("<# ", "<#")
            + "\n進行中の案内がある場合は、そのチャンネルを使用しています。",
        )

    tree.add_command(welcome, guild=discord.Object(id=guild_id))
