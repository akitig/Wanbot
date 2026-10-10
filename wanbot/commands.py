"""テストサーバー限定の動作確認コマンド。"""

from __future__ import annotations

from collections.abc import Iterable

import discord
from discord import app_commands

from wanbot.config import AppConfig
from wanbot.permissions import AccessDenied, is_manager


def status_access_error(
    config: AppConfig,
    guild_id: int | None,
    role_ids: Iterable[int],
) -> str | None:
    guild = config.guild_for(guild_id)

    if guild is None or guild.environment != "test":
        return "このコマンドはテストサーバー専用です。"

    if not is_manager(config, guild_id, role_ids):
        return "この操作は指定された管理者のみ実行できます。"

    return None


def register_status_command(
    tree: app_commands.CommandTree,
    client: discord.Client,
    config: AppConfig,
) -> None:
    async def status(interaction: discord.Interaction) -> None:
        role_ids = (
            [role.id for role in interaction.user.roles]
            if isinstance(interaction.user, discord.Member)
            else []
        )

        error = status_access_error(config, interaction.guild_id, role_ids)
        if error is not None:
            await interaction.response.send_message(error, ephemeral=True)
            return

        # 接続直後の検証中や、異常終了処理中には実行しない。
        if (
            not client.is_ready()
            or getattr(client, "connection_check_failed", True)
            or not getattr(client, "_features_started", False)
        ):
            await interaction.response.send_message(
                "Wanbotは準備中、または利用できない状態です。",
                ephemeral=True,
            )
            return

        guild_config = config.guild_for(interaction.guild_id)
        if guild_config is None:
            raise AccessDenied("このサーバーでは利用できません。")

        configured = sorted(guild_config.enabled_features)
        active = [
            name
            for name in configured
            if client.feature_available(guild_config.guild_id, name)
        ]

        await interaction.response.send_message(
            "Wanbotは稼働中です。\n"
            "環境：テストサーバー\n"
            "管理者ロール：確認済み\n"
            f"設定上の有効な機能：{', '.join(configured) or 'なし'}\n"
            f"起動済みの機能：{', '.join(active) or 'なし'}",
            ephemeral=True,
        )

    for guild in config.guilds.values():
        if guild.environment != "test":
            continue

        command = app_commands.Command(
            name="wanbot_status",
            description="Wanbotの接続状態と管理者権限を確認します",
            callback=status,
        )
        tree.add_command(command, guild=discord.Object(id=guild.guild_id))


class WanbotCommandTree(app_commands.CommandTree):
    async def on_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        from wanbot.logging_config import log_event
        import logging

        original = getattr(error, "original", error)
        log_event(
            "commands",
            "execution_failed",
            guild_id=interaction.guild_id,
            level=logging.ERROR,
            error=original,
        )

        message = "処理できませんでした。管理者へ連絡してください。"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException as send_error:
            log_event(
                "commands",
                "error_response_failed",
                guild_id=interaction.guild_id,
                level=logging.ERROR,
                error=send_error,
            )
