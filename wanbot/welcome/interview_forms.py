"""面談希望日時の入力フォームと、決定前の確認画面。"""

import logging
from datetime import datetime, timezone

import discord

from wanbot.welcome.input_activity import track_input

from wanbot.logging_config import log_event
from wanbot.welcome.interview_dates import (
    DATE_FORMAT,
    InterviewDateError,
    format_date,
    validate_dates,
)


async def notice(interaction, content):
    options = {
        "ephemeral": True,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if interaction.response.is_done():
        await interaction.followup.send(content, **options)
    else:
        await interaction.response.send_message(content, **options)


async def report_error(context, interaction, error):
    log_event(
        "welcome",
        "interview_form_failed",
        guild_id=context.guild_id,
        level=logging.ERROR,
        error=error,
    )
    await notice(
        interaction,
        "処理を完了できませんでした。"
        "希望日時が保存されているか、開始画面から確認してください。",
    )


def preview_text(dates):
    lines = [
        "**面談希望日時の確認**",
        "",
        "以下の希望日時でよろしいですか？",
        "この時点では、面談日時はまだ確定していません。",
        "",
    ]
    for number, value in enumerate(dates, start=1):
        lines.append(f"**第{number}希望**：{format_date(value)}")
    lines.extend([
        "",
        "「この日時で決定する」を押すと、希望日時を提出します。",
        "提出後は、この画面から変更できません。",
    ])
    return "\n".join(lines)


class InterviewDatesModal(discord.ui.Modal):
    def __init__(
        self,
        context,
        *,
        on_submit,
        initial_values=None,
        clock=None,
    ):
        super().__init__(title="面談希望日時の入力", timeout=600)
        self._context = context
        self._submit_handler = on_submit
        self._clock = clock or (lambda: datetime.now(timezone.utc))

        values = (
            ("", "", "")
            if initial_values is None
            else tuple(initial_values)
        )
        if len(values) != 3:
            raise ValueError("Three interview preferences are required")

        self._date_inputs = []
        for number, value in enumerate(values, start=1):
            field = discord.ui.TextInput(
                label=f"第{number}希望日時（日本時間）",
                placeholder="2026-10-20 21:00",
                default=value,
                required=True,
                max_length=40,
                style=discord.TextStyle.short,
            )
            self._date_inputs.append(field)
            self.add_item(field)

    async def on_submit(self, interaction):
        if not await self._context.check(interaction):
            return

        raw_values = tuple(field.value for field in self._date_inputs)
        try:
            dates = validate_dates(raw_values, now=self._clock())
        except InterviewDateError as error:
            await notice(
                interaction,
                str(error) + "\n入力ボタンから入力し直してください。",
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        values = tuple(value.strftime(DATE_FORMAT) for value in dates)
        await self._submit_handler(interaction, values)

    async def on_error(self, interaction, error):
        await report_error(self._context, interaction, error)


class InterviewDatesPreview(discord.ui.View):
    def __init__(
        self,
        context,
        values,
        *,
        on_confirm,
        on_edit,
    ):
        super().__init__(timeout=600)
        self._context = context
        self._values = tuple(values)
        if len(self._values) != 3:
            raise ValueError("Three interview preferences are required")
        self._confirm_handler = on_confirm
        self._edit_handler = on_edit

        confirm = discord.ui.Button(
            label="この日時で決定する",
            style=discord.ButtonStyle.success,
        )
        edit = discord.ui.Button(
            label="修正する",
            style=discord.ButtonStyle.secondary,
        )
        confirm.callback = self.confirm
        edit.callback = self.edit
        self.add_item(confirm)
        self.add_item(edit)
        track_input(context, self, "interview", 600)

    async def confirm(self, interaction):
        if not await self._context.check(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        # 条件の再確認と保存は接続先の処理が担当する。
        await self._confirm_handler(interaction, self._values)

    async def edit(self, interaction):
        if not await self._context.check(interaction):
            return
        # 接続先で、入力済みの日時を入れたModalを開く。
        await self._edit_handler(interaction, self._values)

    async def on_error(self, interaction, error, item):
        await report_error(self._context, interaction, error)
