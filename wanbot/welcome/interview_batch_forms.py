"""第1〜第3希望を、日付と時刻の2フォームでまとめて選択する。"""

from datetime import datetime, timezone

import discord

from wanbot.welcome.input_activity import track_input
from wanbot.welcome.interview_dates import (
    DATE_FORMAT,
    JST,
    InterviewDateError,
)
from wanbot.welcome.interview_forms import notice, report_error
from wanbot.welcome.interview_picker import (
    day_options,
    validate_picker_dates,
)


def available_times(period, *, now):
    """指定日・午前午後の、まだ過ぎていない30分刻みの日時。"""
    try:
        day, half = period.split("|")
        allowed_days = {value for _, value in day_options(now)}
        if day not in allowed_days or half not in ("am", "pm"):
            raise ValueError
        date = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=JST)
    except (ValueError, AttributeError):
        raise InterviewDateError(
            "日付を確認してください。今日・明日・明後日から選べます。"
        ) from None

    first_hour = 0 if half == "am" else 12
    result = []
    for hour in range(first_hour, first_hour + 12):
        for minute in (0, 30):
            candidate = date.replace(hour=hour, minute=minute)
            if candidate > now.astimezone(JST):
                result.append(candidate.strftime(DATE_FORMAT))
    return tuple(result)


def available_periods(*, now):
    result = []
    for label, day in day_options(now):
        for half, text in (("am", "午前"), ("pm", "午後")):
            value = f"{day}|{half}"
            if available_times(value, now=now):
                result.append((f"{label} {text}", value))
    return tuple(result)


def initial_period(value):
    if not value:
        return None
    try:
        date = datetime.strptime(value, DATE_FORMAT)
    except (ValueError, TypeError):
        return None
    half = "am" if date.hour < 12 else "pm"
    return f"{date:%Y-%m-%d}|{half}"


class InterviewBatchPicker(discord.ui.View):
    """フォームを開くボタン。選択済みの内容は修正時にも引き継ぐ。"""

    def __init__(
        self,
        context,
        *,
        on_submit,
        initial_values=None,
        clock=None,
        periods=None,
    ):
        super().__init__(timeout=600)
        self._context = context
        self._submit_handler = on_submit
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._initial_values = (
            ("", "", "") if initial_values is None
            else tuple(initial_values)
        )
        self._periods = None if periods is None else tuple(periods)
        if len(self._initial_values) != 3:
            raise ValueError("Three preferences are required")
        if self._periods is not None and len(self._periods) != 3:
            raise ValueError("Three periods are required")

        button = discord.ui.Button(
            label=(
                "第1〜第3希望の日付を入力する"
                if self._periods is None else
                "第1〜第3希望の時刻を入力する"
            ),
            style=discord.ButtonStyle.primary,
        )
        button.callback = self.open_form
        self.add_item(button)

        if self._periods is not None:
            back = discord.ui.Button(
                label="日付を選び直す",
                style=discord.ButtonStyle.secondary,
            )
            back.callback = self.open_dates
            self.add_item(back)

        track_input(context, self, "interview", 600)

    @property
    def content(self):
        if self._periods is None:
            return (
                "第1〜第3希望をまとめて入力します。\n"
                "まず日付と午前・午後を選び、次に開始時刻を選んでください。\n"
                "時刻は日本時間・30分刻みです。"
            )
        return (
            "日付を選択しました。続いて3つの開始時刻を入力してください。\n"
            "まだ提出されていません。最後の確認画面で決定します。"
        )

    async def open_form(self, interaction):
        await self._open(interaction, self._periods)

    async def open_dates(self, interaction):
        await self._open(interaction, None)

    async def _open(self, interaction, periods):
        if not await self._context.check(interaction):
            return
        try:
            modal = InterviewBatchModal(
                self._context,
                on_submit=self._submit_handler,
                initial_values=self._initial_values,
                periods=periods,
                clock=self._clock,
            )
        except InterviewDateError as error:
            await notice(interaction, str(error))
            return
        await interaction.response.send_modal(modal)

    async def on_error(self, interaction, error, item):
        await report_error(self._context, interaction, error)


class InterviewBatchModal(discord.ui.Modal):
    def __init__(
        self,
        context,
        *,
        on_submit,
        initial_values,
        periods,
        clock,
    ):
        super().__init__(
            title=(
                "面談希望：日付をまとめて選択"
                if periods is None else
                "面談希望：開始時刻をまとめて選択"
            ),
            timeout=600,
        )
        self._context = context
        self._submit_handler = on_submit
        self._initial_values = tuple(initial_values)
        self._periods = None if periods is None else tuple(periods)
        self._clock = clock
        self._fields = []

        now = clock()
        for index in range(3):
            if periods is None:
                pairs = available_periods(now=now)
                default = initial_period(self._initial_values[index])
                label = f"第{index + 1}希望の日付・午前午後"
            else:
                values = available_times(periods[index], now=now)
                pairs = tuple((value[-5:], value) for value in values)
                default = self._initial_values[index]
                day, half = periods[index].split("|")
                half_label = "午前" if half == "am" else "午後"
                label = f"第{index + 1}希望：{day} {half_label}"

            if not pairs:
                raise InterviewDateError(
                    "選べる時刻がなくなりました。日付を選び直してください。"
                )

            field = discord.ui.Select(
                placeholder="選んでください",
                min_values=1,
                max_values=1,
                options=[
                    discord.SelectOption(
                        label=text, value=value, default=value == default
                    )
                    for text, value in pairs
                ],
            )
            self._fields.append(field)
            self.add_item(discord.ui.Label(text=label, component=field))

        track_input(context, self, "interview", 600)

    async def on_submit(self, interaction):
        if not await self._context.check(interaction):
            return
        if any(len(field.values) != 1 for field in self._fields):
            await notice(interaction, "第1〜第3希望をすべて選んでください。")
            return

        values = tuple(field.values[0] for field in self._fields)
        try:
            if self._periods is None:
                for value in values:
                    if not available_times(value, now=self._clock()):
                        raise InterviewDateError(
                            "選べる時刻がなくなりました。日付を選び直してください。"
                        )
            else:
                # フォームを開いてから時間が進んだ場合も再確認する。
                for value, period in zip(values, self._periods):
                    if value not in available_times(period, now=self._clock()):
                        raise InterviewDateError(
                            "選択した時刻は利用できません。入力し直してください。"
                        )
                validate_picker_dates(values, now=self._clock())
        except InterviewDateError as error:
            await notice(
                interaction,
                str(error) + "\n入力ボタンから選び直してください。",
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        if self._periods is not None:
            # 既存の確認画面へ渡す。ここではまだ保存しない。
            await self._submit_handler(interaction, values)
            return

        view = InterviewBatchPicker(
            self._context,
            on_submit=self._submit_handler,
            initial_values=self._initial_values,
            periods=values,
            clock=self._clock,
        )
        await interaction.edit_original_response(
            content=view.content,
            embed=None,
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def on_error(self, interaction, error):
        await report_error(self._context, interaction, error)
