"""面談不参加の回答保存と、再調整・キック承認依頼への接続。"""

from wanbot.welcome.no_show_repository import NoShowConflict


class NoShowFlow:
    """各lockedメソッドはWelcome共通ロック内から呼ぶ。

    管理者の操作は、呼出元で最新の管理者権限を確認する。
    本人回答も呼出元で操作場所・在籍を確認する。
    """

    def __init__(self, *, controller, repository):
        self._controller = controller
        self._repository = repository

    async def _check_session(self):
        controller = self._controller
        await controller._session()
        if await controller._admissions.get(
            controller._context.session_id
        ) is not None:
            raise NoShowConflict("参加承認済みのため、不参加処理はできません。")

    async def _record(self, proposal_id):
        await self._check_session()
        record = await self._repository.get(proposal_id)
        context = self._controller._context
        if record is None or (
            record["guild_id"] != context.guild_id
            or record["session_id"] != context.session_id
            or record["user_id"] != context.user_id
            or record["channel_id"] != context.channel_id
        ):
            raise NoShowConflict("このWelcomeの不参加記録ではありません。")
        return record

    async def _current_plan(self, proposal_id):
        plan = await self._controller._plan()
        if (
            plan is None
            or plan.status != "confirmed"
            or plan.proposal_id != proposal_id
        ):
            raise NoShowConflict("現在の確定済み面談ではありません。")
        return plan

    async def _no_other_kick(self):
        controller = self._controller
        records = await controller._kick_review._repository.for_session(
            controller._context.session_id
        )
        if any(
            record["status"] in (
                "pending", "approved", "executing", "completed"
            )
            for record in records
        ):
            raise NoShowConflict(
                "キック確認依頼があるため、不参加の回答を変更できません。"
            )

    async def begin_locked(self, *, proposal_id, actor_id, now):
        await self._check_session()
        await self._no_other_kick()
        plan = await self._current_plan(proposal_id)
        return await self._repository.begin(
            context=self._controller._context,
            plan=plan,
            actor_id=actor_id,
            now=now,
        )

    async def answer_locked(
        self, *, proposal_id, actor_id, reschedule, now
    ):
        record = await self._record(proposal_id)
        if actor_id != record["user_id"]:
            raise NoShowConflict("本人だけが回答できます。")
        await self._current_plan(proposal_id)
        await self._no_other_kick()

        record = await self._repository.respond(
            proposal_id,
            actor_id=actor_id,
            reschedule=reschedule,
            now=now,
        )
        await self._apply(record)
        return record

    async def confirm_second_locked(self, *, proposal_id, actor_id):
        await self._record(proposal_id)
        await self._current_plan(proposal_id)
        await self._no_other_kick()
        record = await self._repository.confirm_second(
            proposal_id, actor_id=actor_id
        )
        await self._apply(record)
        return record

    async def refresh_locked(self, *, proposal_id, now):
        """期限確認と、回答保存後に中断した処理の再開。"""
        record = await self._record(proposal_id)
        if record["status"] == "waiting":
            await self._current_plan(proposal_id)
            await self._no_other_kick()
            record = await self._repository.expire(proposal_id, now=now)
        await self._apply(record)
        return record

    async def _apply(self, record):
        if record["status"] == "reschedule_requested":
            await self._no_other_kick()
            await self._reschedule(record)
        elif record["status"] == "kick_required":
            # 実際のキックは既存の管理者承認処理だけが担当する。
            await self._controller._kick_review.request_no_show(
                self._controller, record
            )

    async def _reschedule(self, record):
        controller = self._controller
        context = controller._context
        proposal_id = record["proposal_id"]

        # 面談予定は、希望日時を履歴へ移した後で履歴へ移す。
        # この順番により、途中で停止しても元の面談を特定できる。
        archived_plan = await controller._meetings.archived_for_reschedule(
            proposal_id
        )
        archived_request = (
            await controller._repository.archived_for_reschedule(
                proposal_id
            )
        )

        if archived_plan is not None:
            self._check_plan_scope(archived_plan, proposal_id)
            if archived_request is None:
                raise NoShowConflict(
                    "再調整前の希望日時が見つかりません。運営に確認してください。"
                )
            self._check_request_scope(archived_request)
            # すでに切替済み。新たに提出された希望日時や面談には触れない。
            return

        plan = await self._current_plan(proposal_id)
        self._check_plan_scope(plan, proposal_id)

        if archived_request is None:
            request = await controller._repository.get(context.session_id)
            if request is None:
                raise NoShowConflict("再調整前の希望日時が見つかりません。")
            self._check_request_scope(request)
            await controller._repository.archive_for_reschedule(
                request, proposal_id=proposal_id
            )
        else:
            self._check_request_scope(archived_request)

        await controller._meetings.archive_for_reschedule(plan)
        controller._published_meeting = None

    def _check_request_scope(self, request):
        context = self._controller._context
        if (
            request.session_id != context.session_id
            or request.user_id != context.user_id
            or request.channel_id != context.channel_id
        ):
            raise NoShowConflict("再調整前の希望日時の対象が一致しません。")

    def _check_plan_scope(self, plan, proposal_id):
        context = self._controller._context
        if (
            plan.proposal_id != proposal_id
            or plan.welcome_session_id != context.session_id
            or plan.guild_id != context.guild_id
            or plan.user_id != context.user_id
            or plan.channel_id != context.channel_id
            or plan.status != "confirmed"
        ):
            raise NoShowConflict("再調整前の面談の対象が一致しません。")
