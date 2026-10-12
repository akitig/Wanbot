"""日程変更の画面、権限確認、保存処理を接続する。"""

import logging


from wanbot.logging_config import log_event
from wanbot.permissions import AccessDenied
from wanbot.welcome.interview_gate import InterviewNotReady
from wanbot.welcome.schedule_change_flow import ScheduleChangeFlow
from wanbot.welcome.schedule_change_repository import ScheduleChangeConflict
from wanbot.welcome.schedule_change_views import (
    ScheduleChangeMenu,
    acknowledge,
    display,
)


class ScheduleChangeBinding:
    def __init__(self, *, controller, repository):
        self._controller = controller
        self._repository = repository
        self._flow = ScheduleChangeFlow(
            controller=controller, repository=repository
        )
        self._applying = False

    async def _notify(self):
        from wanbot.welcome.schedule_change_notice import ScheduleChangeNotice

        notice = ScheduleChangeNotice(
            controller=self._controller,
            repository=self._repository,
        )
        await notice.refresh_locked()

    async def guard(self):
        if self._applying:
            return
        records = await self._repository.for_session(
            self._controller._context.session_id
        )
        if any(item["status"] == "pending" for item in records):
            raise InterviewNotReady(
                "日程変更を処理中です。少し待ってから開き直してください。"
            )

    async def generation(self):
        records = await self._repository.for_session(
            self._controller._context.session_id
        )
        return tuple(sorted(item["change_id"] for item in records))

    async def _authorize_or_raise(self, interaction, source):
        controller = self._controller
        context = controller._context
        if (
            interaction.guild_id != context.guild_id
            or interaction.channel_id != context.channel_id
        ):
            raise AccessDenied("このWelcome内で操作してください。")

        await controller._session()
        if source == "manager":
            await controller._actor(interaction)
        elif source == "member":
            if interaction.user.id != context.user_id:
                raise AccessDenied("日程変更は参加者本人が操作してください。")
            guild = controller._client.get_guild(context.guild_id)
            if guild is None or guild.unavailable:
                raise InterviewNotReady("サーバーを確認できません。")
            member = await guild.fetch_member(context.user_id)
            if member.bot:
                raise AccessDenied("本人が操作してください。")
        else:
            raise AccessDenied("操作の種類が不正です。")

    async def authorize(self, interaction, source):
        try:
            await self._authorize_or_raise(interaction, source)
            return True
        except Exception as error:
            await self.error(interaction, error)
            return False

    async def error(self, interaction, error):
        await acknowledge(interaction)
        if isinstance(
            error, (AccessDenied, InterviewNotReady, ScheduleChangeConflict)
        ):
            content = str(error)
        else:
            log_event(
                "welcome", "schedule_change_failed",
                guild_id=self._controller._context.guild_id,
                level=logging.ERROR, error=error,
            )
            content = (
                "日程変更を完了できませんでした。"
                "保存済みの変更は再確認します。少し待って開き直してください。"
            )
        await display(interaction, content)

    async def open(self, interaction):
        await acknowledge(interaction)
        controller = self._controller
        context = controller._context
        source = (
            "member" if interaction.user.id == context.user_id else "manager"
        )
        try:
            async with controller._lock:
                await self._authorize_or_raise(interaction, source)
                request = await controller.load_request()
                if request is None:
                    raise InterviewNotReady(
                        "希望日時は未提出です。チェックリストから入力してください。"
                    )
                plan = await controller._plan()
                view = ScheduleChangeMenu(
                    expected=(request, plan),
                    authorize=self.authorize,
                    on_confirm=self.confirm,
                    on_error=self.error,
                )
                controller._views.append(view)
                await display(
                    interaction,
                    "本人は「日程を変更する」、"
                    "管理者は「希望日時の再提出を依頼する」を選んでください。",
                    view,
                )
        except Exception as error:
            await self.error(interaction, error)

    async def _apply(self, change_id):
        # 呼出元は共通ロックを取得済み。
        self._applying = True
        try:
            return await self._flow.apply_locked(change_id)
        finally:
            self._applying = False

    async def confirm(self, interaction, source, expected):
        controller = self._controller
        async with controller._lock:
            await self._authorize_or_raise(interaction, source)
            request = await controller.load_request()
            plan = await controller._plan()
            if request is None or (request, plan) != expected:
                raise ScheduleChangeConflict(
                    "日程の状態が変わっています。最新の一覧から開き直してください。"
                )

            # 変更できない状態では、依頼自体を保存しない。
            if await controller._admissions.get(
                controller._context.session_id
            ) is not None:
                raise ScheduleChangeConflict("参加承認済みのため変更できません。")
            reviews = await controller._kick_review._repository.for_session(
                controller._context.session_id
            )
            if any(
                item["status"] in (
                    "pending", "approved", "executing", "completed"
                )
                for item in reviews
            ):
                raise ScheduleChangeConflict("キック確認が進行中です。")
            no_show = getattr(controller, "_no_show", None)
            if no_show is not None:
                await no_show.guard_input()

            record = await self._repository.begin(
                context=controller._context,
                request=request,
                proposal_id=plan.proposal_id if plan is not None else None,
                actor_id=interaction.user.id,
                source=source,
                now=controller._clock(),
            )
            await self._apply(record["change_id"])
            await controller._refresh_meeting_checklist()
            await self._notify()
            await display(
                interaction,
                "日程変更を受け付けました。\n"
                "参加手続き一覧の「面談の希望日時を入力する」から"
                "再入力してください。",
            )

    async def refresh(self):
        controller = self._controller
        if controller._closed or not controller._context.is_available():
            return
        async with controller._lock:
            session = await controller._welcome.get_active(
                controller._context.user_id
            )
            if (
                session is None
                or session.state != "active"
                or session.session_id != controller._context.session_id
                or session.channel_id != controller._context.channel_id
            ):
                return
            records = await self._repository.for_session(
                controller._context.session_id
            )
            changed = False
            for record in records:
                if record["status"] == "pending":
                    await self._apply(record["change_id"])
                    changed = True
            if changed:
                await controller._refresh_meeting_checklist()
            await self._notify()
