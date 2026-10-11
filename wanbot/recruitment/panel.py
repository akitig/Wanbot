from __future__ import annotations

from collections.abc import Awaitable, Callable

import discord

from wanbot.recruitment.forms import notice, report_error


BeginHandler = Callable[[discord.Interaction], Awaitable[None]]


def panel_embed() -> discord.Embed:
    return discord.Embed(
        title="VALORANT募集",
        description=(
            "下のボタンから募集を作成できます。\n\n"
            "通知先はGachi・Enjoy、または両方を選択できます。\n"
            "自分が持っていないロールにも募集できます。\n\n"
            "内容を確認し、「完了」を押すと投稿されます。"
        ),
        colour=discord.Colour.red(),
    )


class RecruitmentPanelView(discord.ui.View):
    def __init__(
        self,
        *,
        guild_id: int,
        channel_id: int,
        on_begin: BeginHandler,
    ) -> None:
        super().__init__(timeout=None)
        self._guild_id = guild_id
        self._channel_id = channel_id
        self._on_begin = on_begin
        self.begin.custom_id = (
            f"wanbot:recruitment:begin:{guild_id}:{channel_id}"
        )

    @discord.ui.button(
        label="VALORANTの募集を行う",
        style=discord.ButtonStyle.success,
    )
    async def begin(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if (
            interaction.guild_id != self._guild_id
            or interaction.channel_id != self._channel_id
        ):
            await notice(
                interaction,
                "設定された募集チャンネルから操作してください。",
            )
            return

        await self._on_begin(interaction)

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item,
    ) -> None:
        await report_error(interaction, error, self._guild_id)
