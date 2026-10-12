"""面談希望の提出後、管理者の日程選択と参加確認を行う。"""

import logging

import discord

from wanbot.logging_config import log_event
from wanbot.welcome.admission_actions import AdmissionActions
from wanbot.permissions import AccessDenied, require_manager
from wanbot.welcome.interview_controller import InterviewController
from wanbot.welcome.interview_dates import format_date
from wanbot.welcome.interview_gate import InterviewNotReady
from wanbot.welcome.meeting_plan import MeetingPlanConflict
from wanbot.welcome.no_show_repository import NoShowConflict
from wanbot.welcome.meeting_views import (
    MeetingConfirmView,
    MeetingDateView,
    SecondManagerResponseView,
    SecondManagerView,
)


class MeetingInterviewController(AdmissionActions, InterviewController):
    def __init__(
        self, *, config, meeting_repository, meeting_voice=None,
        admissions=None, admission_service=None, admission_questions=None,
        admission_diagnosis=None, **kwargs
    ):
        super().__init__(**kwargs)
        self._meeting_config = config
        self._meetings = meeting_repository
        self._meeting_voice = meeting_voice
        self._admissions = admissions
        self._admission_service = admission_service
        self._admission_questions = admission_questions
        self._admission_diagnosis = admission_diagnosis
        self._published_meeting = None
        self._meeting_completion = None

    def set_meeting_completion_handler(self, handler):
        self._meeting_completion = handler

    async def _plan(self):
        plan = await self._meetings.get(self._context.session_id)
        if plan is not None and (
            plan.welcome_session_id != self._context.session_id
            or plan.guild_id != self._context.guild_id
            or plan.user_id != self._context.user_id
            or plan.channel_id != self._context.channel_id
        ):
            raise InterviewNotReady("面談の対象が一致しません。")
        return plan

    async def load_meeting(self):
        """チェックリストの更新時に、管理者向け投稿も復旧する。"""
        await self._session()
        request = await self.load_request()
        if request is None:
            return None
        plan = await self._plan()
        await self._publish_meeting(request, plan)
        return plan

    async def _show_saved(self, interaction, request):
        await super()._show_saved(interaction, request)
        await self.load_meeting()

    async def _member(self, user_id):
        guild = self._client.get_guild(self._context.guild_id)
        if guild is None:
            raise InterviewNotReady("サーバーを確認できません。")
        member = await guild.fetch_member(user_id)
        if member.bot or member.id == self._context.user_id:
            raise AccessDenied("面談担当の管理者を選んでください。")
        require_manager(
            self._meeting_config,
            self._context.guild_id,
            (role.id for role in member.roles),
            feature="welcome",
        )
        return member

    async def _actor(self, interaction, owner_id=None):
        await self._session()
        if (
            interaction.guild_id != self._context.guild_id
            or interaction.channel_id != self._context.channel_id
        ):
            raise AccessDenied("このWelcome内で操作してください。")
        if owner_id is not None and interaction.user.id != owner_id:
            raise AccessDenied("この操作は指名された管理者のみ実行できます。")
        try:
            return await self._member(interaction.user.id)
        except AccessDenied:
            raise AccessDenied(
                "管理者以外押せません、対応まで少々お待ちください。"
            ) from None

    async def _notice(self, interaction, content, view=None):
        await interaction.edit_original_response(
            content=content,
            embed=None,
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _meeting_error(self, interaction, error):
        if isinstance(
            error, (AccessDenied, InterviewNotReady, MeetingPlanConflict, NoShowConflict)
        ):
            content = str(error)
        elif isinstance(error, discord.NotFound):
            content = "対象のメンバーまたはチャンネルが見つかりません。"
        else:
            log_event(
                "welcome",
                "meeting_operation_failed",
                guild_id=self._context.guild_id,
                level=logging.ERROR,
                error=error,
            )
            content = (
                "操作を完了できませんでした。"
                "Welcomeの最新の面談案内を確認してください。"
                "保存済みの操作は取り消されません。"
            )
        await self._notice(interaction, content)

    async def _authorize(self, interaction):
        try:
            await self._actor(interaction)
        except Exception as error:
            await self._meeting_error(interaction, error)
            return False
        return True

    def _owner_authorizer(self, owner_id):
        async def authorize(interaction):
            try:
                await self._actor(interaction, owner_id)
            except Exception as error:
                await self._meeting_error(interaction, error)
                return False
            return True
        return authorize

    async def _operate(self, interaction, operation, owner_id=None):
        try:
            async with self._lock:
                actor = await self._actor(interaction, owner_id)
                await operation(actor)
        except Exception as error:
            await self._meeting_error(interaction, error)

    async def _selectable(self, expected_id):
        request = await self.load_request()
        if request is None:
            raise InterviewNotReady("面談希望日時がまだ提出されていません。")
        binding = getattr(self, "_no_show", None)
        if isinstance(expected_id, tuple):
            expected_id, submitted_at = expected_id
            if request.confirmed_at.isoformat() != submitted_at:
                raise MeetingPlanConflict(
                    "希望日時が変更されています。最新の案内から選び直してください。"
                )
        elif binding is not None and await binding.has_rescheduled():
            raise MeetingPlanConflict(
                "再調整前の画面です。最新の案内から選び直してください。"
            )
        plan = await self._plan()
        current_id = plan.proposal_id if plan is not None else None
        if current_id != expected_id:
            raise MeetingPlanConflict("状態が変わりました。最新の案内から選び直してください。")
        if plan is not None and plan.status != "declined":
            raise MeetingPlanConflict("参加確認中、または確定済みです。")
        return request

    async def choose_date(self, interaction, preference, expected_id):
        async def operation(actor):
            request = await self._selectable(expected_id)
            if preference not in (1, 2, 3):
                raise MeetingPlanConflict("希望日時を選び直してください。")
            if request.preferences[preference - 1] <= self._clock():
                raise MeetingPlanConflict("この希望日時は過ぎています。")

            async def choose_second(current, second_id):
                await self.preview_meeting(
                    current, actor.id, preference, second_id, expected_id
                )

            view = SecondManagerView(
                authorize=self._owner_authorizer(actor.id),
                on_choose=choose_second,
            )
            self._views.append(view)
            await self._notice(
                interaction,
                f"選択日時：{format_date(request.preferences[preference - 1])}\n"
                "2人目の管理者を選んでください。\n"
                "1人で面談する場合は「2人目なし」を押してください。",
                view,
            )

        await self._operate(interaction, operation)

    async def preview_meeting(
        self, interaction, owner_id, preference, second_id, expected_id
    ):
        async def operation(actor):
            request = await self._selectable(expected_id)
            if second_id == actor.id:
                raise AccessDenied("2人目には別の管理者を選んでください。")
            if second_id is not None:
                await self._member(second_id)

            async def confirm(current):
                await self.confirm_meeting(
                    current, owner_id, preference, second_id, expected_id
                )

            async def back(current):
                await self.choose_date(current, preference, expected_id)

            view = MeetingConfirmView(
                authorize=self._owner_authorizer(owner_id),
                on_confirm=confirm,
                on_back=back,
            )
            self._views.append(view)
            second_text = (
                f"<@{second_id}>" if second_id is not None else "なし"
            )
            await self._notice(
                interaction,
                f"日時：{format_date(request.preferences[preference - 1])}\n"
                f"担当：<@{actor.id}>\n"
                f"2人目：{second_text}\n\n"
                "決定すると、2人目がいる場合は参加可否を確認します。",
                view,
            )

        await self._operate(interaction, operation, owner_id)

    async def confirm_meeting(
        self, interaction, owner_id, preference, second_id, expected_id
    ):
        async def operation(actor):
            request = await self._selectable(expected_id)
            second = (
                await self._member(second_id)
                if second_id is not None else None
            )
            await self._session()
            plan = await self._meetings.propose(
                request=request,
                actor_id=actor.id,
                actor_role_ids=tuple(role.id for role in actor.roles),
                preference=preference,
                second_manager_id=second_id,
                second_manager_role_ids=(
                    tuple(role.id for role in second.roles)
                    if second is not None else ()
                ),
                expected_proposal_id=(
                    expected_id[0] if isinstance(expected_id, tuple)
                    else expected_id
                ),
                now=self._clock(),
            )
            await self._publish_meeting(request, plan)
            await self._refresh_meeting_checklist()
            await self._notice(
                interaction,
                "面談日時を確定しました。"
                if plan.status == "confirmed"
                else "保存しました。2人目の管理者へ参加確認を送りました。",
            )

        await self._operate(interaction, operation, owner_id)

    async def respond_meeting(self, interaction, proposal_id, accepted):
        async def operation(actor):
            current = await self._plan()
            if current is None or current.proposal_id != proposal_id:
                raise MeetingPlanConflict("この参加確認は現在の依頼ではありません。")
            if actor.id != current.second_manager_id:
                raise AccessDenied("指名された2人目の管理者のみ回答できます。")
            if accepted:
                # 承諾時にも1人目が在籍し、管理者であることを確認する。
                await self._member(current.first_manager_id)
            await self._session()
            plan = await self._meetings.respond(
                session_id=self._context.session_id,
                proposal_id=proposal_id,
                actor_id=actor.id,
                actor_role_ids=tuple(role.id for role in actor.roles),
                accepted=accepted,
                now=self._clock(),
            )
            request = await self.load_request()
            await self._publish_meeting(request, plan)
            await self._refresh_meeting_checklist()
            await self._notice(
                interaction,
                "参加を受け付けました。面談日時が確定しました。"
                if accepted else
                "不参加を受け付けました。管理者が担当者を選び直せます。",
            )

        await self._operate(interaction, operation)

    async def _refresh_meeting_checklist(self):
        if self._meeting_completion is not None:
            await self._meeting_completion()

    async def _publish_meeting(self, request, plan):
        if request is None:
            raise InterviewNotReady("提出済みの希望日時を確認できません。")
        key = (
            plan.proposal_id if plan is not None else None,
            plan.status if plan is not None else None,
        )
        if self._published_meeting == key:
            return

        await self._session()
        channel = await self._client.fetch_channel(self._context.channel_id)
        if (
            not isinstance(channel, discord.TextChannel)
            or channel.guild.id != self._context.guild_id
            or channel.id != self._context.channel_id
            or channel.topic != (
                f"wanbot:welcome:{self._context.guild_id}:"
                f"{self._context.session_id}"
            )
            or self._client.user is None
        ):
            raise InterviewNotReady("Welcomeチャンネルを確認できません。")

        expected_id = (
            plan.proposal_id if plan is not None else None,
            request.confirmed_at.isoformat(),
        )

        async def choose(interaction, preference):
            await self.choose_date(interaction, preference, expected_id)

        if plan is None or plan.status == "declined":
            content = (
                "管理者の方は、参加可能な希望日時を選んでください。"
                if plan is None else
                "2人目の管理者が参加できないため、担当者を選び直してください。"
            )
            view = MeetingDateView(
                session_id=self._context.session_id,
                authorize=self._authorize,
                on_choose=choose,
            )
        elif plan.status == "waiting_second":
            content = (
                f"<@{plan.second_manager_id}> この日時で面談に参加できますか？\n"
                f"日時：{format_date(plan.meeting_at)}\n"
                f"1人目の担当：<@{plan.first_manager_id}>"
            )
            view = SecondManagerResponseView(
                proposal_id=plan.proposal_id,
                authorize=self._owner_authorizer(plan.second_manager_id),
                on_respond=self.respond_meeting,
            )
        else:
            if self._meeting_voice is None:
                raise InterviewNotReady("面談VCの作成処理が設定されていません。")
            await self._session()
            voice = await self._meeting_voice.ensure(plan)
            await self._session()
            content = (
                f"<@{self._context.user_id}> 面談日時が確定しました。\n"
                f"{format_date(plan.meeting_at)}\n"
                f"担当：<@{plan.first_manager_id}>"
            )
            if plan.second_manager_id is not None:
                content += f"、<@{plan.second_manager_id}>"
            content += (
                f"\n面談VC：<#{voice.id}>"
                f"\nhttps://discord.com/channels/{plan.guild_id}/{voice.id}"
                "\nこの日時に、こちらのVCで面談を行います。"
            )
            view = self.admission_view(plan.proposal_id)

        # 提案ごとに識別し、再起動後も同じ投稿を再利用する。
        suffix = plan.proposal_id if plan is not None else "initial"
        marker = f"wanbot:welcome:meeting:{self._context.session_id}:{suffix}"
        embed = discord.Embed(title="面談の日程調整")
        for number, date in enumerate(request.preferences, 1):
            embed.add_field(
                name=f"第{number}希望", value=format_date(date), inline=False
            )
        embed.set_footer(text=marker)

        confirmed = plan is not None and plan.status == "confirmed"
        message = None
        previous_messages = []

        async for candidate in channel.history(limit=None):
            if (
                candidate.author.id != self._client.user.id
                or candidate.webhook_id is not None
            ):
                continue

            has_marker = any(
                item.footer.text == marker for item in candidate.embeds
            )
            if confirmed:
                # 確定通知には希望日時一覧や内部識別子を表示しない。
                # このWelcome内の同じ通知本文で再利用対象を判別する。
                if candidate.content == content and not candidate.embeds:
                    if message is None:
                        message = candidate
                elif has_marker:
                    previous_messages.append(candidate)
            elif has_marker:
                message = candidate
                break

        await self._session()
        mentions = discord.AllowedMentions.none()
        if message is None and plan is not None and plan.status == "waiting_second":
            mentions = discord.AllowedMentions(
                everyone=False,
                roles=False,
                users=[discord.Object(id=plan.second_manager_id)],
                replied_user=False,
            )
        if message is None and confirmed:
            mentions = discord.AllowedMentions(
                everyone=False,
                roles=False,
                users=[
                    discord.Object(id=user_id)
                    for user_id in (
                        plan.user_id,
                        plan.first_manager_id,
                        plan.second_manager_id,
                    )
                    if user_id is not None
                ],
                replied_user=False,
            )

        options = dict(
            content=content,
            embed=None if confirmed else embed,
            view=view,
            allowed_mentions=mentions,
        )
        try:
            if message is None:
                await channel.send(**options)
            else:
                await message.edit(**options)
        except BaseException:
            if view is not None:
                view.stop()
            raise

        # 確定通知を送れた後で、元の参加確認を終了表示へ変更する。
        for previous in previous_messages:
            await previous.edit(
                content="面談の参加確認は完了しました。",
                embed=None,
                view=None,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        if view is not None:
            self._views.append(view)

        if plan is None or plan.status == "declined":
            await self._notify_managers(channel, plan)

        self._published_meeting = key

    async def _notify_managers(self, channel, plan):
        guild_config = self._meeting_config.guild_for(
            self._context.guild_id
        )
        if guild_config is None or not guild_config.admin_role_ids:
            raise InterviewNotReady("管理者ロールが設定されていません。")

        # 初回と、提案ごとの辞退について、それぞれ一度だけ通知する。
        notice_key = (
            "initial" if plan is None
            else f"declined:{plan.proposal_id}"
        )
        marker = (
            f"wanbot:welcome:meeting-notice:"
            f"{self._context.session_id}:{notice_key}"
        )

        async for candidate in channel.history(limit=None):
            if (
                candidate.author.id == self._client.user.id
                and candidate.webhook_id is None
                and any(
                    item.footer.text == marker
                    for item in candidate.embeds
                )
            ):
                return

        await self._session()
        role_ids = sorted(guild_config.admin_role_ids)
        mentions = " ".join(f"<@&{role_id}>" for role_id in role_ids)
        description = (
            "面談希望日時が提出されました。"
            "上の「面談の日程調整」から、参加可能な日時を選んでください。"
            if plan is None else
            "2人目の管理者が参加できないため、"
            "上の「面談の日程調整」から担当者を選び直してください。"
        )

        embed = discord.Embed(
            title="管理者の対応をお願いします",
            description=(
                description
                + "\n日程選択ボタンは管理者のみ操作できます。"
            ),
        )
        embed.set_footer(text=marker)

        await channel.send(
            content=mentions,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(
                everyone=False,
                users=False,
                roles=[
                    discord.Object(id=role_id)
                    for role_id in role_ids
                ],
                replied_user=False,
            ),
        )
