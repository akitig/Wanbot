"""通常の日程変更の入口と、本人・管理者向けの確認画面。"""

import discord


async def acknowledge(interaction):
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)


async def display(interaction, content, view=None):
    await interaction.edit_original_response(
        content=content,
        embed=None,
        view=view,
        allowed_mentions=discord.AllowedMentions.none(),
    )


class ScheduleChangeConfirmView(discord.ui.View):
    def __init__(
        self, *, owner_id, source, expected,
        authorize, on_confirm, on_error,
    ):
        super().__init__(timeout=600)
        self._owner_id = owner_id
        self._source = source
        self._expected = expected
        self._authorize = authorize
        self._on_confirm = on_confirm
        self._on_error = on_error

        confirm = discord.ui.Button(
            label="変更して再入力する"
            if source == "member" else "再提出を依頼する",
            style=discord.ButtonStyle.danger,
        )
        cancel = discord.ui.Button(
            label="キャンセル",
            style=discord.ButtonStyle.secondary,
        )
        confirm.callback = self.confirm
        cancel.callback = self.cancel
        self.add_item(confirm)
        self.add_item(cancel)

    async def _check(self, interaction):
        await acknowledge(interaction)
        if interaction.user.id != self._owner_id:
            await display(
                interaction, "この確認画面を開いた本人だけが操作できます。"
            )
            return False
        if self.is_finished():
            await display(
                interaction, "この確認画面は終了しています。開き直してください。"
            )
            return False
        return await self._authorize(interaction, self._source)

    async def confirm(self, interaction):
        if not await self._check(interaction):
            return
        # このハンドラーで共通ロックを取得し、対象・権限を再検証する。
        await self._on_confirm(
            interaction, self._source, self._expected
        )
        self.stop()

    async def cancel(self, interaction):
        if not await self._check(interaction):
            return
        await display(interaction, "日程変更をキャンセルしました。")
        self.stop()

    async def on_error(self, interaction, error, item):
        await acknowledge(interaction)
        await self._on_error(interaction, error)


class ScheduleChangeMenu(discord.ui.View):
    def __init__(
        self, *, expected, authorize, on_confirm, on_error,
    ):
        super().__init__(timeout=600)
        self._expected = expected
        self._authorize = authorize
        self._on_confirm = on_confirm
        self._on_error = on_error
        self._confirmations = []

        for source, label in (
            ("member", "日程を変更する"),
            ("manager", "希望日時の再提出を依頼する"),
        ):
            button = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.secondary,
            )

            async def callback(interaction, selected=source):
                await self.open_confirmation(interaction, selected)

            button.callback = callback
            self.add_item(button)

    async def open_confirmation(self, interaction, source):
        await acknowledge(interaction)
        if self.is_finished():
            await display(
                interaction, "この画面は終了しています。開き直してください。"
            )
            return
        if source not in ("member", "manager"):
            raise ValueError("Unknown schedule change source")
        if not await self._authorize(interaction, source):
            return

        view = ScheduleChangeConfirmView(
            owner_id=interaction.user.id,
            source=source,
            expected=self._expected,
            authorize=self._authorize,
            on_confirm=self._on_confirm,
            on_error=self._on_error,
        )
        self._confirmations.append(view)
        await display(
            interaction,
            "提出済みの希望日時を取り消し、入力し直せる状態に戻します。\n"
            "面談が確定済みの場合、その予定も取り消されます。\n"
            "再提出後は、管理者の日程選択と担当者の確認をやり直します。\n\n"
            "日程を変更しますか？"
            if source == "member" else
            "本人へ希望日時の再提出を依頼します。\n"
            "提出済みの希望日時と、確定済みの場合は面談予定を取り消します。\n"
            "再提出後は、日程選択と担当者の確認をやり直します。\n\n"
            "再提出を依頼しますか？",
            view,
        )

    def stop(self):
        for view in self._confirmations:
            view.stop()
        super().stop()

    async def on_timeout(self):
        self.stop()

    async def on_error(self, interaction, error, item):
        await acknowledge(interaction)
        await self._on_error(interaction, error)
