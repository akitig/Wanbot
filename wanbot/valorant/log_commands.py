"""管理者限定の診断ログ表示とページ切り替え。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import discord
from discord import app_commands

from wanbot.config import AppConfig
from wanbot.logging_config import log_event
from wanbot.permissions import AccessDenied, require_feature, require_manager
from wanbot.valorant.log_presentation import format_log_pages
from wanbot.valorant.questions import QuestionSet


async def _notice(interaction: discord.Interaction, text: str) -> None:
    options = {
        "ephemeral": True,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if interaction.response.is_done():
        await interaction.followup.send(text, **options)
    else:
        await interaction.response.send_message(text, **options)


class LogPages(discord.ui.View):
    def __init__(
        self,
        pages: tuple[str, ...],
        *,
        owner_id: int,
        guild_id: int,
        can_access: Callable[[discord.Interaction], bool],
    ) -> None:
        if not pages:
            raise ValueError("表示するページがありません。")
        super().__init__(timeout=300)
        self._pages = pages
        self._owner_id = owner_id
        self._guild_id = guild_id
        self._can_access = can_access
        self._index = 0
        self._lock = asyncio.Lock()
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.previous.disabled = self._index == 0
        self.next_page.disabled = self._index == len(self._pages) - 1

    def _allowed(self, interaction: discord.Interaction) -> bool:
        return (
            not self.is_finished()
            and interaction.user.id == self._owner_id
            and self._can_access(interaction)
        )

    async def _move(self, interaction: discord.Interaction, step: int) -> None:
        if not self._allowed(interaction):
            await _notice(interaction, "この診断ログは操作できません。")
            return

        await interaction.response.defer(thinking=False)
        async with self._lock:
            if not self._allowed(interaction):
                await _notice(interaction, "この診断ログは操作できません。")
                return

            previous = self._index
            self._index = max(0, min(len(self._pages) - 1, previous + step))
            self._update_buttons()
            try:
                await interaction.edit_original_response(
                    content=self._pages[self._index],
                    view=self,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except BaseException:
                self._index = previous
                self._update_buttons()
                raise

    @discord.ui.button(label="前へ", style=discord.ButtonStyle.secondary)
    async def previous(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._move(interaction, -1)

    @discord.ui.button(label="次へ", style=discord.ButtonStyle.primary)
    async def next_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._move(interaction, 1)

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item,
    ) -> None:
        log_event(
            "valorant_diagnosis",
            "log_page_failed",
            guild_id=self._guild_id,
            level=logging.ERROR,
            error=error,
        )
        try:
            await _notice(
                interaction,
                "表示を更新できませんでした。コマンドをもう一度実行してください。",
            )
        except discord.HTTPException as send_error:
            log_event(
                "valorant_diagnosis",
                "log_error_notice_failed",
                guild_id=self._guild_id,
                level=logging.ERROR,
                error=send_error,
            )


def register_log_command(
    tree: app_commands.CommandTree,
    config: AppConfig,
    guild_id: int,
    *,
    question_set: QuestionSet,
    is_available: Callable[[], bool],
    load_record: Callable[[int], Awaitable[dict | None]],
) -> app_commands.Command:
    require_feature(config, guild_id, "valorant_diagnosis")

    def can_access(interaction: discord.Interaction) -> bool:
        if interaction.guild_id != guild_id or not is_available():
            return False
        roles = (
            [role.id for role in interaction.user.roles]
            if isinstance(interaction.user, discord.Member)
            else []
        )
        try:
            require_manager(
                config,
                guild_id,
                roles,
                feature="valorant_diagnosis",
            )
        except AccessDenied:
            return False
        return True

    @app_commands.guild_only()
    @app_commands.describe(
        member="診断を確認するメンバー。user_idとの同時指定はできません",
        user_id="退出済みの人などのユーザーID。数字で入力してください",
    )
    async def valo_role_log(
        interaction: discord.Interaction,
        member: discord.Member | None = None,
        user_id: str | None = None,
    ) -> None:
        if not can_access(interaction):
            await _notice(interaction, "権限がないか、診断機能が利用できません。")
            return

        if (member is None) == (user_id is None):
            await _notice(
                interaction,
                "member または user_id のどちらか一方を指定してください。",
            )
            return

        if member is not None:
            if member.guild.id != guild_id:
                await _notice(interaction, "別サーバーのメンバーは指定できません。")
                return
            target_id = member.id
        else:
            raw = user_id.strip()
            if (
                not raw.isascii()
                or not raw.isdecimal()
                or len(raw) > 20
                or not 0 < int(raw) < 2**64
            ):
                await _notice(interaction, "有効なユーザーIDを指定してください。")
                return
            target_id = int(raw)

        await interaction.response.defer(ephemeral=True, thinking=True)
        record = await load_record(target_id)

        # 保存データを読んでいる間に、利用不可になった場合も表示しない。
        if not can_access(interaction):
            await _notice(interaction, "現在、この診断ログは表示できません。")
            return

        if record is None:
            await interaction.edit_original_response(
                content="このサーバーには、対象者の保存済み診断がありません。",
                view=None,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        pages = format_log_pages(record, question_set)
        view = LogPages(
            pages,
            owner_id=interaction.user.id,
            guild_id=guild_id,
            can_access=can_access,
        )
        try:
            await interaction.edit_original_response(
                content=pages[0],
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except BaseException:
            view.stop()
            raise

    command = app_commands.Command(
        name="valo_role_log",
        description="保存済みの最新のVALORANT診断と回答を確認します",
        callback=valo_role_log,
    )
    tree.add_command(command, guild=discord.Object(id=guild_id))
    return command
