"""3つの希望日時を個別に入力し、一覧からまとめて提出する。"""

import asyncio
from datetime import datetime, timezone

import discord

from wanbot.welcome.input_activity import track_input
from wanbot.welcome.interview_dates import (
    DATE_FORMAT,
    JST,
    InterviewDateError,
    format_date,
)
from wanbot.welcome.interview_forms import notice, report_error
from wanbot.welcome.interview_picker import (
    day_options,
    validate_picker_dates,
)


def selected_datetime(day, hour, minute, *, now):
    allowed_days = {value for _, value in day_options(now)}
    if day not in allowed_days:
        raise InterviewDateError(
            "日付が変わりました。今日・明日・明後日から選び直してください。"
        )
    if hour not in {str(value) for value in range(24)}:
        raise InterviewDateError("時を選んでください。")
    if minute not in ("00", "30"):
        raise InterviewDateError("分を選んでください。")

    value = f"{day} {int(hour):02d}:{minute}"
    parsed = datetime.strptime(value, DATE_FORMAT).replace(tzinfo=JST)
    if parsed <= now.astimezone(JST):
        raise InterviewDateError("現在より後の開始時刻を選んでください。")
    return value


class InterviewPreferencesView(discord.ui.View):
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
        self._values = list(initial_values or ("", "", ""))
        if len(self._values) != 3:
            raise ValueError("Three preferences are required")
        self._draft_revision = 0
        self._draft_lock = asyncio.Lock()
        self._sending = False
        self._expired = False
        self._build()
        track_input(context, self, "interview", 600)

    @property
    def content(self):
        lines = ["**面談希望日時**", ""]
        for number, value in enumerate(self._values, 1):
            if value:
                date = datetime.strptime(value, DATE_FORMAT).replace(tzinfo=JST)
                lines.append(f"✅ 第{number}希望：{format_date(date)}")
            else:
                lines.append(f"□ 第{number}希望：未入力")
        lines.extend([
            "",
            "各ボタンから日時を入力・修正してください。",
            "日本時間・30分刻みです。",
            "3つ入力してから「希望日時を送信する」を押してください。",
            "送信するまでは提出されません。",
        ])
        return "\n".join(lines)

    def _build(self):
        self.clear_items()
        revision = self._draft_revision
        for index, value in enumerate(self._values):
            button = discord.ui.Button(
                label=f"第{index + 1}希望：{'修正する' if value else '入力する'}",
                style=discord.ButtonStyle.secondary if value
                else discord.ButtonStyle.primary,
                row=index,
            )

            async def open_form(interaction, selected=index):
                if not await self._context.check(interaction):
                    return
                async with self._draft_lock:
                    if (
                        self._expired
                        or self._sending
                        or revision != self._draft_revision
                    ):
                        await notice(
                            interaction,
                            "最新の一覧から操作してください。期限切れの場合は開き直してください。",
                        )
                        return
                    await interaction.response.send_modal(
                        PreferenceModal(self, selected, revision)
                    )

            button.callback = open_form
            self.add_item(button)

        send = discord.ui.Button(
            label="希望日時を送信する",
            style=discord.ButtonStyle.success,
            disabled=not all(self._values),
            row=3,
        )
        send.callback = self.send_preferences
        self.add_item(send)

    async def save_draft(self, interaction, index, revision, value):
        async with self._draft_lock:
            if (
                self._expired
                or self._sending
                or revision != self._draft_revision
            ):
                await notice(
                    interaction,
                    "入力画面が更新されています。最新の一覧から入力し直してください。",
                )
                return
            if value in (
                existing for position, existing in enumerate(self._values)
                if position != index
            ):
                await notice(
                    interaction,
                    "他の希望と同じ日時です。別の日時を選んでください。",
                )
                return

            self._values[index] = value
            self._draft_revision += 1
            self._build()
            await interaction.edit_original_response(
                content=self.content,
                embed=None,
                view=self,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    async def send_preferences(self, interaction):
        if not await self._context.check(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self._draft_lock:
            if self._expired or self._sending:
                await notice(
                    interaction,
                    "送信中、または期限切れです。最新の案内を確認してください。",
                )
                return
            try:
                values = tuple(self._values)
                validate_picker_dates(values, now=self._clock())
            except InterviewDateError as error:
                await notice(interaction, str(error))
                return
            self._sending = True

        try:
            # 質問・診断・ルールと保存済み状態は接続先で再確認する。
            await self._submit_handler(interaction, values)
        finally:
            async with self._draft_lock:
                self._sending = False

    async def on_timeout(self):
        self._expired = True

    async def on_error(self, interaction, error, item):
        await report_error(self._context, interaction, error)


class PreferenceModal(discord.ui.Modal):
    def __init__(self, parent, index, revision):
        super().__init__(title=f"第{index + 1}希望日時", timeout=600)
        self._preferences_view = parent
        self._preference_index = index
        self._expected_revision = revision

        current = parent._values[index]
        initial = (
            datetime.strptime(current, DATE_FORMAT).replace(tzinfo=JST)
            if current else None
        )
        self._day = self._select(
            "日付",
            day_options(parent._clock()),
            initial.strftime("%Y-%m-%d") if initial else None,
        )
        self._hour = self._select(
            "時（日本時間）",
            tuple((f"{hour:02d}時", str(hour)) for hour in range(24)),
            str(initial.hour) if initial else None,
        )
        self._minute = self._select(
            "分",
            (("00分", "00"), ("30分", "30")),
            initial.strftime("%M") if initial else None,
        )
        track_input(parent._context, self, "interview", 600)

    def _select(self, label, pairs, initial):
        field = discord.ui.Select(
            placeholder="選んでください",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=text, value=value, default=value == initial
                )
                for text, value in pairs
            ],
        )
        self.add_item(discord.ui.Label(text=label, component=field))
        return field

    async def on_submit(self, interaction):
        parent = self._preferences_view
        if not await parent._context.check(interaction):
            return
        fields = (self._day, self._hour, self._minute)
        if any(len(field.values) != 1 for field in fields):
            await notice(interaction, "日付・時・分をすべて選んでください。")
            return

        try:
            value = selected_datetime(
                *(field.values[0] for field in fields),
                now=parent._clock(),
            )
        except InterviewDateError as error:
            await notice(interaction, str(error))
            return

        # フォームを開いた元の一覧を更新する。
        await interaction.response.defer(thinking=False)
        await parent.save_draft(
            interaction,
            self._preference_index,
            self._expected_revision,
            value,
        )

    async def on_error(self, interaction, error):
        await report_error(
            self._preferences_view._context, interaction, error
        )
