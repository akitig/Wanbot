"""不参加の確認投稿・操作・定期確認を既存の面談処理へ接続する。"""

import logging

import discord

from wanbot.logging_config import log_event
from wanbot.welcome.no_show_flow import NoShowFlow
from wanbot.welcome.no_show_repository import NoShowConflict
from wanbot.welcome.no_show_views import NoShowView, no_show_message


class NoShowBinding:
    def __init__(self, *, controller, repository):
        self._controller = controller
        self._repository = repository
        self._flow = NoShowFlow(
            controller=controller, repository=repository
        )
        self._posted = {}
        self._views = {}

    async def _records(self):
        context = self._controller._context
        return [
            item for item in await self._repository.all_records()
            if item["session_id"] == context.session_id
            and item["guild_id"] == context.guild_id
            and item["user_id"] == context.user_id
            and item["channel_id"] == context.channel_id
        ]

    async def guard_admission(self, proposal_id):
        record = await self._repository.get(proposal_id)
        if record is None:
            return

        if record["status"] == "kick_required":
            requests = await (
                self._controller._kick_review._repository.for_session(
                    self._controller._context.session_id
                )
            )
            marker = f'（面談ID：{proposal_id}）'
            if any(
                item["status"] == "cancelled"
                and item["reason"].endswith(marker)
                for item in requests
            ):
                return

        raise NoShowConflict(
            "不参加の確認・再調整が進行中です。"
            "現在の不参加確認を先に完了してください。"
        )

    async def guard_input(self):
        for record in await self._records():
            if record["status"] != "reschedule_requested":
                raise NoShowConflict(
                    "不参加の確認が進行中です。最新の確認投稿をご覧ください。"
                )
            proposal_id = record["proposal_id"]
            controller = self._controller
            if (
                await controller._repository.archived_for_reschedule(
                    proposal_id
                ) is None
                or await controller._meetings.archived_for_reschedule(
                    proposal_id
                ) is None
            ):
                raise NoShowConflict(
                    "再調整への切り替え中です。少し待って開き直してください。"
                )

    async def has_rescheduled(self):
        return any(
            record["status"] == "reschedule_requested"
            for record in await self._records()
        )

    def add_report_button(self, view, proposal_id):
        button = discord.ui.Button(
            label="面談不参加",
            style=discord.ButtonStyle.secondary,
            custom_id=f"wanbot:no-show:report:{proposal_id}",
        )

        async def callback(interaction):
            await interaction.response.defer(ephemeral=True, thinking=True)

            async def operation(actor):
                record = await self._flow.begin_locked(
                    proposal_id=proposal_id,
                    actor_id=actor.id,
                    now=self._controller._clock(),
                )
                await self._publish(record)
                await self._controller._notice(
                    interaction,
                    "不参加確認を表示しました。"
                    "再クリックしても回答期限は延長されません。",
                )

            await self._controller._operate(interaction, operation)

        button.callback = callback
        view.add_item(button)

    async def _authorize_applicant(self, interaction):
        controller = self._controller
        try:
            await controller._session()
            if (
                interaction.guild_id != controller._context.guild_id
                or interaction.channel_id != controller._context.channel_id
                or interaction.user.id != controller._context.user_id
            ):
                raise NoShowConflict("対象のWelcomeで本人が回答してください。")
            guild = controller._client.get_guild(
                controller._context.guild_id
            )
            if guild is None:
                raise NoShowConflict("サーバーを確認できません。")
            await guild.fetch_member(controller._context.user_id)
            return True
        except Exception as error:
            await self._error(interaction, error)
            return False

    async def _answer(self, interaction, proposal_id, reschedule):
        controller = self._controller
        try:
            async with controller._lock:
                if not await self._authorize_applicant(interaction):
                    return
                record = await self._flow.answer_locked(
                    proposal_id=proposal_id,
                    actor_id=interaction.user.id,
                    reschedule=reschedule,
                    now=controller._clock(),
                )
                await self._publish(record)
                await controller._refresh_meeting_checklist()
                await controller._notice(
                    interaction,
                    "再調整を受け付けました。参加手続きの一覧から、"
                    "面談希望日時を入力し直してください。"
                    if record["status"] == "reschedule_requested"
                    else
                    "管理者へキックの承認を依頼しました。"
                    "まだキックは実行していません。",
                )
        except Exception as error:
            await self._error(interaction, error)

    async def _confirm(self, interaction, proposal_id):
        controller = self._controller

        async def operation(actor):
            record = await self._flow.confirm_second_locked(
                proposal_id=proposal_id, actor_id=actor.id
            )
            await self._publish(record)
            await controller._notice(
                interaction,
                "管理者専用チャンネルへキック承認を依頼しました。"
                "まだキックは実行していません。",
            )

        await controller._operate(interaction, operation)

    async def _channel(self):
        controller = self._controller
        await controller._session()
        context = controller._context
        channel = await controller._client.fetch_channel(context.channel_id)
        if (
            not isinstance(channel, discord.TextChannel)
            or channel.id != context.channel_id
            or channel.guild.id != context.guild_id
            or channel.topic != (
                f"wanbot:welcome:{context.guild_id}:{context.session_id}"
            )
            or controller._client.user is None
        ):
            raise NoShowConflict("Welcomeチャンネルを確認できません。")
        return channel

    async def _publish(self, record):
        proposal_id = record["proposal_id"]
        status = record["status"]
        if self._posted.get(proposal_id) == status:
            return

        controller = self._controller
        channel = await self._channel()
        marker = f"wanbot:welcome:no-show:{proposal_id}"
        message = None
        async for candidate in channel.history(limit=None):
            if (
                candidate.author.id == controller._client.user.id
                and candidate.webhook_id is None
                and any(
                    embed.footer.text == marker
                    for embed in candidate.embeds
                )
            ):
                message = candidate
                break

        view = None
        content = ""
        mentions = discord.AllowedMentions.none()
        if status in ("waiting", "second_confirmation"):
            text = no_show_message(record)
            view = NoShowView(
                record=record,
                authorize=(
                    self._authorize_applicant
                    if status == "waiting" else controller._authorize
                ),
                on_answer=self._answer,
                on_confirm=self._confirm,
            )
            if status == "waiting":
                content = f'<@{record["user_id"]}>'
                if message is None:
                    mentions = discord.AllowedMentions(
                        everyone=False,
                        roles=False,
                        users=[discord.Object(id=record["user_id"])],
                        replied_user=False,
                    )
            else:
                config = controller._meeting_config.guild_for(
                    record["guild_id"]
                )
                if config is None:
                    view.stop()
                    raise NoShowConflict("管理者設定を確認できません。")
                content = " ".join(
                    f"<@&{value}>" for value in sorted(config.admin_role_ids)
                )
                if message is None:
                    mentions = discord.AllowedMentions(
                        everyone=False,
                        users=False,
                        roles=[
                            discord.Object(id=value)
                            for value in config.admin_role_ids
                        ],
                        replied_user=False,
                    )
        elif status == "reschedule_requested":
            text = (
                "再調整を受け付けました。\n"
                "参加手続きの一覧から、面談希望日時を入力し直してください。\n"
                "不参加による再調整は今回の1回のみです。"
            )
        else:
            text = (
                "不参加の確認は終了しました。\n"
                "キックについては管理者専用チャンネルで判断します。"
                "この投稿からはキックを実行しません。"
            )

        embed = discord.Embed(title="面談不参加の確認", description=text)
        embed.set_footer(text=marker)
        try:
            await controller._session()
            options = dict(
                content=content,
                embed=embed,
                view=view,
                allowed_mentions=mentions,
            )
            if message is None:
                await channel.send(**options)
            else:
                await message.edit(**options)
        except BaseException:
            if view is not None:
                view.stop()
            raise

        old = self._views.pop(proposal_id, None)
        if old is not None:
            old.stop()
        if view is not None:
            self._views[proposal_id] = view
            controller._views.append(view)
        self._posted[proposal_id] = status

    async def refresh(self):
        controller = self._controller
        if controller._closed or not controller._context.is_available():
            return
        async with controller._lock:
            await controller._session()
            if await controller._admissions.get(
                controller._context.session_id
            ) is not None:
                return
            for record in await self._records():
                try:
                    proposal_id = record["proposal_id"]
                    complete = (
                        record["status"] == "reschedule_requested"
                        and await controller._meetings.archived_for_reschedule(
                            proposal_id
                        ) is not None
                        and await controller._repository.archived_for_reschedule(
                            proposal_id
                        ) is not None
                    )
                    if not complete:
                        record = await self._flow.refresh_locked(
                            proposal_id=proposal_id,
                            now=controller._clock(),
                        )
                    await self._publish(record)
                except Exception as error:
                    self._log(error)

    def _log(self, error):
        log_event(
            "welcome",
            "no_show_processing_failed",
            guild_id=self._controller._context.guild_id,
            level=logging.ERROR,
            error=error,
        )

    async def _error(self, interaction, error):
        if isinstance(error, NoShowConflict):
            await self._controller._notice(interaction, str(error))
        else:
            await self._controller._meeting_error(interaction, error)
