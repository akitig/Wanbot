"""管理者向けのVALORANT診断コマンド。"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import discord
from discord import app_commands

from wanbot.config import AppConfig
from wanbot.logging_config import log_event
from wanbot.permissions import AccessDenied, require_feature, require_manager
from wanbot.storage.json_store import StorageError
from wanbot.valorant.session import SessionError


SendDiagnosis = Callable[[discord.Member, int], Awaitable[None]]


def register_diagnosis_command(
    tree: app_commands.CommandTree,
    config: AppConfig,
    guild_id: int,
    *,
    is_available: Callable[[], bool],
    send_diagnosis: SendDiagnosis,
    resend_diagnosis: SendDiagnosis | None = None,
) -> app_commands.Command:
    # 無効なサーバーにコマンドを登録しない。
    require_feature(config, guild_id, "valorant_diagnosis")

    @app_commands.guild_only()
    @app_commands.describe(
        member="診断DMを送るサーバーメンバー",
        resend="Trueなら現在の質問または保存済みの結果を再表示します",
    )
    async def valo_role(
        interaction: discord.Interaction,
        member: discord.Member,
        resend: bool = False,
    ) -> None:
        options = {
            "ephemeral": True,
            "allowed_mentions": discord.AllowedMentions.none(),
        }

        if interaction.guild_id != guild_id:
            await interaction.response.send_message(
                "このサーバーでは実行できません。",
                **options,
            )
            return

        role_ids = (
            [role.id for role in interaction.user.roles]
            if isinstance(interaction.user, discord.Member)
            else []
        )
        try:
            require_manager(
                config,
                interaction.guild_id,
                role_ids,
                feature="valorant_diagnosis",
            )
        except AccessDenied:
            await interaction.response.send_message(
                "この操作は設定された管理者のみ実行できます。",
                **options,
            )
            return

        if member.guild.id != guild_id or member.bot:
            await interaction.response.send_message(
                "このサーバーにいる、Bot以外のメンバーを指定してください。",
                **options,
            )
            return

        if not is_available():
            await interaction.response.send_message(
                "診断機能は準備中、または利用できない状態です。",
                **options,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        if not is_available():
            await interaction.followup.send(
                "診断機能は準備中、または利用できない状態です。",
                **options,
            )
            return

        try:
            # 管理者のIDは入力値ではなく、実行した本人から取得する。
            if resend:
                if resend_diagnosis is None:
                    raise SessionError("再表示は利用できません。")
                await resend_diagnosis(member, interaction.user.id)
            else:
                await send_diagnosis(member, interaction.user.id)
        except SessionError:
            if resend:
                message = (
                    "再表示できませんでした。期限内の診断、または"
                    "保存済みの結果があるか確認してください。"
                )
            else:
                message = (
                    "診断を開始できませんでした。"
                    "進行中、または結果保存待ちの診断がないか確認してください。"
                )
        except discord.Forbidden as error:
            _log_failure(guild_id, "diagnosis_dm_forbidden", error)
            message = (
                "診断DMを送信できませんでした。"
                "対象者のDM受信設定や、Botのブロック状況を確認してください。"
            )
        except discord.HTTPException as error:
            _log_failure(guild_id, "diagnosis_dm_failed", error)
            message = (
                "Discordへの送信を完了できませんでした。"
                "対象者への到着状況を確認してください。"
            )
        except StorageError as error:
            _log_failure(guild_id, "diagnosis_start_storage_failed", error)
            message = "診断データの保存処理を完了できませんでした。"
        else:
            message = (
                "診断をDMに再表示しました。"
                if resend
                else "診断DMを送信しました。"
            )

        await interaction.followup.send(message, **options)

    command = app_commands.Command(
        name="valo_role",
        description="指定したメンバーにVALORANT診断のDMを送信します",
        callback=valo_role,
    )
    tree.add_command(command, guild=discord.Object(id=guild_id))
    return command


def _log_failure(guild_id: int, event: str, error: Exception) -> None:
    log_event(
        "valorant_diagnosis",
        event,
        guild_id=guild_id,
        level=logging.ERROR,
        error=error,
    )
