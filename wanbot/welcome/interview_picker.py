"""今日・明日・明後日と開始時刻から、面談希望日時を選ぶ。"""

import asyncio
from datetime import datetime, timedelta, timezone

import discord

from wanbot.welcome.input_activity import track_input

from wanbot.welcome.interview_dates import (
    DATE_FORMAT,
    JST,
    InterviewDateError,
    validate_dates,
)
from wanbot.welcome.interview_forms import notice, report_error


def day_options(now):
    today = now.astimezone(JST).date()
    return tuple(
        (
            f"{label}（{(today + timedelta(days=offset)):%m/%d}）",
            (today + timedelta(days=offset)).isoformat(),
        )
        for offset, label in enumerate(("今日", "明日", "明後日"))
    )


def validate_picker_dates(values, *, now):
    dates = validate_dates(values, now=now)
    allowed = {value for _, value in day_options(now)}
    for number, value in enumerate(dates, start=1):
        if value.date().isoformat() not in allowed:
            raise InterviewDateError(
                f"第{number}希望は、今日・明日・明後日から選んでください。"
            )
        if value.minute not in (0, 30):
            raise InterviewDateError(
                f"第{number}希望は、00分または30分から選んでください。"
            )
    return dates


class InterviewDatePicker(discord.ui.View):
    def __init__(
        self,
        context,
        *,
        on_submit,
        initial_values=None,
        clock=None,
    ):
        super().__init__(timeout=600)
        self._context = context
        self._submit_handler = on_submit
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._choices = list(initial_values or ("", "", ""))
        if len(self._choices) != 3:
            raise ValueError("Three preferences are required")
        self._index = 0
        self._day = None
        self._hour = None
        self._minute = None
        self._revision = 0
        self._finished = False
        self._lock = asyncio.Lock()
        self._load_choice()
        self._build()
        track_input(context, self, "interview", 600)

    def _load_choice(self):
        self._day = self._hour = self._minute = None
        value = self._choices[self._index]
        if not value:
            return
        try:
            parsed = datetime.strptime(value, DATE_FORMAT)
        except ValueError:
            return

        self._day = parsed.date().isoformat()
        self._hour = str(parsed.hour)
        if parsed.minute in (0, 30):
            self._minute = str(parsed.minute)

    def _current_value(self):
        if None in (self._day, self._hour, self._minute):
            raise InterviewDateError("日付・時・分をすべて選んでください。")
        return (
            f"{self._day} "
            f"{int(self._hour):02d}:{int(self._minute):02d}"
        )

    @property
    def content(self):
        lines = [
            f"**第{self._index + 1}希望の開始日時を選んでください。**",
            "日本時間・24時間表記です。日付、時、分を選びます。",
            "",
        ]
        for number, value in enumerate(self._choices, start=1):
            lines.append(f"第{number}希望：{value or '未選択'}")
        return "\n".join(lines)

    def _select(self, placeholder, pairs, selected, field, row):
        options = [
            discord.SelectOption(
                label=label,
                value=value,
                default=(value == selected),
            )
            for label, value in pairs
        ]
        menu = discord.ui.Select(
            placeholder=placeholder,
            options=options,
            min_values=1,
            max_values=1,
            row=row,
        )
        revision = self._revision
        allowed = {value for _, value in pairs}

        async def callback(interaction):
            if not await self._context.check(interaction):
                return
            await interaction.response.defer(thinking=False)
            async with self._lock:
                if self._finished or revision != self._revision:
                    await notice(
                        interaction, "画面が更新されています。もう一度選んでください。"
                    )
                    return
                value = menu.values[0]
                if value not in allowed:
                    return
                setattr(self, field, value)
                await self._refresh(interaction)

        menu.callback = callback
        self.add_item(menu)

    def _build(self):
        self.clear_items()
        self._revision += 1
        days = day_options(self._clock())
        if self._day not in {value for _, value in days}:
            self._day = None

        self._select("日付を選ぶ", days, self._day, "_day", 0)
        self._select(
            "開始時刻：時",
            [(f"{hour:02d}時", str(hour)) for hour in range(24)],
            self._hour,
            "_hour",
            1,
        )
        self._select(
            "開始時刻：分",
            [("00分", "0"), ("30分", "30")],
            self._minute,
            "_minute",
            2,
        )
        revision = self._revision
        button = discord.ui.Button(
            label="確認画面へ" if self._index == 2 else "次の希望へ",
            style=discord.ButtonStyle.primary,
            row=3,
        )

        async def advance(interaction):
            if not await self._context.check(interaction):
                return
            await interaction.response.defer(thinking=False)
            async with self._lock:
                if self._finished or revision != self._revision:
                    await notice(
                        interaction, "画面が更新されています。もう一度操作してください。"
                    )
                    return
                try:
                    value = self._current_value()
                    parsed = datetime.strptime(value, DATE_FORMAT).replace(
                        tzinfo=JST
                    )
                    now = self._clock()
                    if parsed <= now.astimezone(JST):
                        raise InterviewDateError(
                            "現在より後の開始時刻を選んでください。"
                        )
                    if self._day not in {
                        day for _, day in day_options(now)
                    }:
                        raise InterviewDateError(
                            "日付が変わりました。日付を選び直してください。"
                        )

                    self._choices[self._index] = value
                    if self._index == 2:
                        validate_picker_dates(self._choices, now=now)
                except InterviewDateError as error:
                    await notice(interaction, str(error))
                    await self._refresh(interaction)
                    return

                if self._index == 2:
                    self._finished = True
                    await self._submit_handler(
                        interaction, tuple(self._choices)
                    )
                    self.stop()
                    return

                self._index += 1
                self._load_choice()
                await self._refresh(interaction)

        button.callback = advance
        self.add_item(button)

        if self._index > 0:
            back = discord.ui.Button(
                label="前の希望へ",
                style=discord.ButtonStyle.secondary,
                row=3,
            )

            async def go_back(interaction):
                if not await self._context.check(interaction):
                    return
                await interaction.response.defer(thinking=False)
                async with self._lock:
                    if self._finished or revision != self._revision:
                        return
                    try:
                        self._choices[self._index] = self._current_value()
                    except InterviewDateError:
                        pass
                    self._index -= 1
                    self._load_choice()
                    await self._refresh(interaction)

            back.callback = go_back
            self.add_item(back)

    async def _refresh(self, interaction):
        self._build()
        await interaction.edit_original_response(
            content=self.content,
            embed=None,
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def on_error(self, interaction, error, item):
        await report_error(self._context, interaction, error)
