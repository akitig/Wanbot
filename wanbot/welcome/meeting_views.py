"""面談の日程・担当者選択と、参加確認の画面。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import discord


Check = Callable[[discord.Interaction], Awaitable[bool]]
Action = Callable[[discord.Interaction], Awaitable[None]]
ChooseDate = Callable[[discord.Interaction, int], Awaitable[None]]
ChooseManager = Callable[
    [discord.Interaction, int | None], Awaitable[None]
]
Respond = Callable[
    [discord.Interaction, str, bool], Awaitable[None]
]


class MeetingView(discord.ui.View):
    def __init__(self, *, authorize: Check, timeout: float | None):
        super().__init__(timeout=timeout)
        self._authorize_meeting = authorize

    async def run_action(
        self,
        interaction: discord.Interaction,
        action: Action,
    ) -> None:
        # 権限確認や保存に時間がかかっても操作を受け付けられるようにする。
        if not interaction.response.is_done():
            await interaction.response.defer(
                ephemeral=True,
                thinking=True,
            )

        if not await self._authorize_meeting(interaction):
            return

        await action(interaction)

    def add_action(
        self,
        *,
        label: str,
        custom_id: str,
        action: Action,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        row: int = 0,
    ) -> None:
        button = discord.ui.Button(
            label=label,
            custom_id=custom_id,
            style=style,
            row=row,
        )

        async def callback(interaction: discord.Interaction) -> None:
            await self.run_action(interaction, action)

        button.callback = callback
        self.add_item(button)

    async def on_error(self, interaction, error, item) -> None:
        message = (
            "面談の操作を完了できませんでした。"
            "最新の案内からもう一度お試しください。"
        )
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(
                message,
                ephemeral=True,
            )


class MeetingDateView(MeetingView):
    """共有投稿に置く、管理者向けの日程選択ボタン。"""

    def __init__(
        self,
        *,
        session_id: str,
        authorize: Check,
        on_choose: ChooseDate,
    ):
        super().__init__(authorize=authorize, timeout=None)

        for preference in (1, 2, 3):
            async def choose(
                interaction: discord.Interaction,
                selected: int = preference,
            ) -> None:
                await on_choose(interaction, selected)

            self.add_action(
                label=f"第{preference}希望で参加可能",
                custom_id=(
                    f"wanbot:meeting:date:{session_id}:{preference}"
                ),
                action=choose,
                style=discord.ButtonStyle.primary,
            )


class SecondManagerView(MeetingView):
    """日程を選んだ管理者だけに表示する担当者選択画面。"""

    def __init__(
        self,
        *,
        authorize: Check,
        on_choose: ChooseManager,
    ):
        super().__init__(authorize=authorize, timeout=600)

        selector = discord.ui.UserSelect(
            placeholder="2人目の管理者を選んでください",
            min_values=1,
            max_values=1,
            row=0,
        )

        async def select_manager(
            interaction: discord.Interaction,
        ) -> None:
            async def choose(current: discord.Interaction) -> None:
                if len(selector.values) != 1:
                    await current.followup.send(
                        "管理者を1人選んでください。",
                        ephemeral=True,
                    )
                    return
                await on_choose(current, selector.values[0].id)

            await self.run_action(interaction, choose)

        selector.callback = select_manager
        self.add_item(selector)

        async def without_second(
            interaction: discord.Interaction,
        ) -> None:
            await on_choose(interaction, None)

        self.add_action(
            label="2人目なし",
            custom_id="wanbot:meeting:no_second",
            action=without_second,
            row=1,
        )


class MeetingConfirmView(MeetingView):
    """保存する前の最終確認。"""

    def __init__(
        self,
        *,
        authorize: Check,
        on_confirm: Action,
        on_back: Action,
    ):
        super().__init__(authorize=authorize, timeout=600)
        self.add_action(
            label="決定",
            custom_id="wanbot:meeting:confirm",
            action=on_confirm,
            style=discord.ButtonStyle.success,
        )
        self.add_action(
            label="選び直す",
            custom_id="wanbot:meeting:back",
            action=on_back,
        )


class SecondManagerResponseView(MeetingView):
    """指名された2人目の管理者が参加可否を回答する。"""

    def __init__(
        self,
        *,
        proposal_id: str,
        authorize: Check,
        on_respond: Respond,
    ):
        super().__init__(authorize=authorize, timeout=None)

        for accepted, label, style in (
            (True, "参加できます", discord.ButtonStyle.success),
            (False, "参加できません", discord.ButtonStyle.danger),
        ):
            async def respond(
                interaction: discord.Interaction,
                answer: bool = accepted,
            ) -> None:
                await on_respond(interaction, proposal_id, answer)

            self.add_action(
                label=label,
                custom_id=(
                    f"wanbot:meeting:respond:{proposal_id}:"
                    f"{int(accepted)}"
                ),
                action=respond,
                style=style,
            )
