"""面談希望日時の条件確認・入力・確定・Welcomeへの表示。"""

import logging
from datetime import datetime, timezone

import discord

from wanbot.logging_config import log_event
from wanbot.welcome.input_activity import finish_input
from wanbot.welcome.interview_dates import (
    InterviewDateError,
    format_date,
)
from wanbot.welcome.interview_forms import (
    InterviewDatesPreview,
    preview_text,
)
from wanbot.welcome.interview_gate import InterviewNotReady
from wanbot.welcome.interview_preferences_view import InterviewPreferencesView
from wanbot.welcome.interview_picker import validate_picker_dates
from wanbot.welcome.interview_repository import InterviewRequestConflict
from wanbot.welcome.no_show_repository import NoShowConflict


class InterviewController:
    def __init__(
        self,
        *,
        client,
        context,
        welcome_repository,
        repository,
        gate,
        lock,
        clock=None,
    ):
        self._client = client
        self._context = context
        self._welcome = welcome_repository
        self._repository = repository
        self._gate = gate
        self._lock = lock
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._views = []
        self._closed = False

    async def load_request(self):
        request = await self._repository.get(self._context.session_id)
        if request is not None and (
            request.session_id != self._context.session_id
            or request.user_id != self._context.user_id
            or request.channel_id != self._context.channel_id
        ):
            raise InterviewNotReady("希望日時の対象が一致しません。")
        return request

    async def _session(self):
        if self._closed or not self._context.is_available():
            raise InterviewNotReady("現在、この案内は利用できません。")
        session = await self._welcome.get_active(self._context.user_id)
        if (
            session is None
            or session.state != "active"
            or session.session_id != self._context.session_id
            or session.user_id != self._context.user_id
            or session.channel_id != self._context.channel_id
        ):
            raise InterviewNotReady("このWelcomeは現在の案内ではありません。")
        schedule = getattr(self, "_schedule_change", None)
        if schedule is not None:
            await schedule.guard()
        return session

    async def _display(self, interaction, content, view=None):
        if view is None:
            finish_input(self._context, "interview")
        await interaction.edit_original_response(
            content=content,
            embed=None,
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _error(self, interaction, error):
        if isinstance(
            error,
            (
                InterviewNotReady, InterviewDateError,
                InterviewRequestConflict, NoShowConflict,
            ),
        ):
            content = str(error)
        else:
            log_event(
                "welcome",
                "interview_operation_failed",
                guild_id=self._context.guild_id,
                level=logging.ERROR,
                error=error,
            )
            content = (
                "処理を完了できませんでした。"
                "「面談希望日時を入力・確認する」から開き直してください。"
                "保存済みの場合は、決定した希望日時を再表示します。"
            )
        await self._display(interaction, content)

    async def begin(self, interaction):
        if not await self._context.check(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            async with self._lock:
                await self._session()
                request = await self._repository.get(self._context.session_id)
                if request is not None:
                    await self._show_saved(interaction, request)
                    return

                binding = getattr(self, "_no_show", None)
                if binding is not None:
                    await binding.guard_input()
                await self._gate.check()
                view = discord.ui.View(timeout=600)
                button = discord.ui.Button(
                    label="希望日時を入力する",
                    style=discord.ButtonStyle.primary,
                )
                button.callback = self.edit
                view.add_item(button)
                self._views.append(view)
                await self._display(
                    interaction,
                    "面談可能な日時を、第三希望まで教えてください。\n"
                    "今日・明日・明後日から選べます。\n"
                    "開始時刻は日本時間・30分単位で選んでください。",
                    view,
                )
        except Exception as error:
            await self._error(interaction, error)

    async def edit(self, interaction, values=None):
        if not await self._context.check(interaction):
            return
        if self._closed:
            await interaction.response.send_message(
                "この入力画面は終了しています。",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            async with self._lock:
                await self._session()
                request = await self._repository.get(self._context.session_id)
                if request is not None:
                    await self._show_saved(interaction, request)
                    return
                binding = getattr(self, "_no_show", None)
                if binding is not None:
                    await binding.guard_input()
                await self._gate.check()

                schedule = getattr(self, "_schedule_change", None)
                generation = (
                    await schedule.generation() if schedule is not None else None
                )

                async def submit(current, submitted_values):
                    await self.confirm(
                        current, submitted_values,
                        expected_generation=generation,
                    )

                view = InterviewPreferencesView(
                    self._context,
                    on_submit=submit,
                    initial_values=values,
                    clock=self._clock,
                )
                self._views.append(view)
                await self._display(interaction, view.content, view)
        except Exception as error:
            await self._error(interaction, error)

    async def preview(self, interaction, values):
        if not await self._context.check(interaction):
            return
        try:
            async with self._lock:
                await self._session()
                request = await self._repository.get(self._context.session_id)
                if request is not None:
                    await self._show_saved(interaction, request)
                    return

                binding = getattr(self, "_no_show", None)
                if binding is not None:
                    await binding.guard_input()
                await self._gate.check()
                dates = validate_picker_dates(values, now=self._clock())
                view = InterviewDatesPreview(
                    self._context,
                    values,
                    on_confirm=self.confirm,
                    on_edit=self.edit,
                )
                self._views.append(view)
                await self._display(interaction, preview_text(dates), view)
        except Exception as error:
            await self._error(interaction, error)

    async def confirm(
        self, interaction, values, *, expected_generation=None
    ):
        if not await self._context.check(interaction):
            return
        try:
            async with self._lock:
                session = await self._session()
                schedule = getattr(self, "_schedule_change", None)
                if expected_generation is not None and schedule is not None:
                    if await schedule.generation() != expected_generation:
                        raise InterviewNotReady(
                            "日程が変更されています。入力画面を開き直してください。"
                        )
                request = await self._repository.get(self._context.session_id)

                if request is None:
                    binding = getattr(self, "_no_show", None)
                    if binding is not None:
                        await binding.guard_input()
                    await self._gate.check()
                    # 通信待ちの間に時刻が進むため、現在時刻で検証する。
                    validate_picker_dates(values, now=self._clock())
                    session = await self._session()
                    request = await self._repository.confirm(
                        session, values, now=self._clock()
                    )

                # 連続クリックや再表示では、保存済みの内容を使う。
                await self._show_saved(interaction, request)
        except Exception as error:
            await self._error(interaction, error)

    async def _show_saved(self, interaction, request):
        if (
            request.session_id != self._context.session_id
            or request.user_id != self._context.user_id
            or request.channel_id != self._context.channel_id
        ):
            raise InterviewNotReady("保存された希望日時の対象が一致しません。")

        channel = await self._client.fetch_channel(self._context.channel_id)
        if (
            not isinstance(channel, discord.TextChannel)
            or channel.id != self._context.channel_id
            or channel.guild.id != self._context.guild_id
            or channel.topic != (
                f"wanbot:welcome:{self._context.guild_id}:"
                f"{self._context.session_id}"
            )
            or self._client.user is None
        ):
            raise InterviewNotReady("Welcomeチャンネルを確認できません。")

        marker = f"wanbot:welcome:interview:{request.session_id}"
        embed = discord.Embed(title="面談希望日時")
        for number, date in enumerate(request.preferences, start=1):
            embed.add_field(
                name=f"第{number}希望",
                value=format_date(date),
                inline=False,
            )
        embed.set_footer(text=marker)

        # 保存後・投稿後に停止した場合も、同じ投稿を再利用する。
        message = None
        async for candidate in channel.history(limit=None):
            if (
                candidate.author.id == self._client.user.id
                and candidate.channel.id == channel.id
                and candidate.webhook_id is None
                and any(item.footer.text == marker for item in candidate.embeds)
            ):
                message = candidate
                break

        await self._session()
        options = {
            "content": (
                "面談希望日時を受け付けました。\n"
                "管理者の返事をお待ちください。\n"
                "面談日時は、管理者との調整後に確定します。"
            ),
            "embed": embed,
            "allowed_mentions": discord.AllowedMentions.none(),
        }
        if message is None:
            message = await channel.send(**options)
        else:
            await message.edit(**options)

        await self._display(
            interaction,
            "面談希望日時は提出済みです。管理者の返事をお待ちください。\n"
            f"https://discord.com/channels/{self._context.guild_id}/"
            f"{self._context.channel_id}/{message.id}",
        )

    def close(self):
        self._closed = True
        for view in self._views:
            view.stop()
        self._views.clear()
