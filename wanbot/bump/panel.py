"""BUMPパネルの表示と、実行方法を案内するボタン。"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

import discord

from wanbot.bump.repository import BumpState
from wanbot.logging_config import log_event


GuideHandler = Callable[[discord.Interaction], Awaitable[None]]


def panel_embed(state: BumpState, *, now: datetime) -> discord.Embed:
    if state.is_available(now):
        return discord.Embed(
            title="🟢 BUMPできます",
            description=(
                "DISBOARDでサーバーの表示順を上げられます。\n"
                "下のボタンから、BUMPの実行方法を確認してください。"
            ),
            color=discord.Color.green(),
        )

    timestamp = int(state.next_bump_at.timestamp())
    return discord.Embed(
        title="⏳ BUMP待機中",
        description=(
            f"次回BUMP可能：<t:{timestamp}:F>\n"
            f"あと <t:{timestamp}:R>"
        ),
        color=discord.Color.greyple(),
    )


def command_guide(command_id: int | None) -> str:
    if command_id is None:
        return (
            "このチャンネルで `/bump` を入力し、\n"
            "DISBOARDのコマンドを選んで実行してください。"
        )

    if type(command_id) is not int or not 0 < command_id < 2**64:
        raise ValueError("有効なコマンドIDを指定してください。")

    return (
        f"BUMPはこちら：</bump:{command_id}>\n"
        "DISBOARDのコマンドを選んで実行してください。"
    )


async def _notice(interaction: discord.Interaction, content: str) -> None:
    options = {
        "ephemeral": True,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if interaction.response.is_done():
        await interaction.followup.send(content, **options)
    else:
        await interaction.response.send_message(content, **options)


class BumpPanelView(discord.ui.View):
    def __init__(
        self,
        *,
        guild_id: int,
        channel_id: int,
        available: bool,
        on_guide: GuideHandler,
    ) -> None:
        super().__init__(timeout=None)
        self._guild_id = guild_id
        self._channel_id = channel_id
        self._on_guide = on_guide

        self.open_command.custom_id = (
            f"wanbot:bump:guide:{guild_id}:{channel_id}"
        )
        self.open_command.label = "BUMPする" if available else "BUMP待機中"
        self.open_command.style = (
            discord.ButtonStyle.success
            if available
            else discord.ButtonStyle.secondary
        )
        self.open_command.disabled = not available

    @discord.ui.button(label="BUMPする")
    async def open_command(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if (
            interaction.guild_id != self._guild_id
            or interaction.channel_id != self._channel_id
        ):
            await _notice(
                interaction,
                "このボタンは設定されたBUMPチャンネルで使用してください。",
            )
            return

        # 実際の受付時にも、呼び出し先で最新の待機状態を確認する。
        await self._on_guide(interaction)

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item,
    ) -> None:
        log_event(
            "bump",
            "guide_failed",
            guild_id=self._guild_id,
            level=logging.ERROR,
            error=error,
        )
        try:
            await _notice(
                interaction,
                "BUMPの案内を表示できませんでした。時間をおいてお試しください。",
            )
        except discord.HTTPException as notice_error:
            log_event(
                "bump",
                "guide_error_notice_failed",
                guild_id=self._guild_id,
                level=logging.ERROR,
                error=notice_error,
            )
