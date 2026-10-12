"""通常の日程変更で、古い希望日時と予定を順番に履歴へ移す。"""

from datetime import datetime

from wanbot.welcome.schedule_change_repository import ScheduleChangeConflict


class ScheduleChangeFlow:
    def __init__(self, *, controller, repository):
        self._controller = controller
        self._repository = repository

    def _check_request(self, request, record):
        if request is None or (
            request.session_id != record["session_id"]
            or request.user_id != record["user_id"]
            or request.channel_id != record["channel_id"]
            or request.confirmed_at
            != datetime.fromisoformat(record["submission_at"])
        ):
            raise ScheduleChangeConflict(
                "変更対象の希望日時が一致しません。"
            )

    def _check_plan(self, plan, record):
        if plan is None or (
            plan.proposal_id != record["proposal_id"]
            or plan.welcome_session_id != record["session_id"]
            or plan.guild_id != record["guild_id"]
            or plan.user_id != record["user_id"]
            or plan.channel_id != record["channel_id"]
        ):
            raise ScheduleChangeConflict(
                "変更対象の面談予定が一致しません。"
            )

    async def apply_locked(self, change_id):
        """本人・管理者の権限確認後、Welcome共通ロック内から呼ぶ。"""
        controller = self._controller
        context = controller._context
        records = await self._repository.for_session(context.session_id)
        matching = [
            item for item in records if item["change_id"] == change_id
        ]
        if len(matching) != 1:
            raise ScheduleChangeConflict("変更依頼が見つかりません。")
        record = matching[0]

        if (
            record["guild_id"] != context.guild_id
            or record["user_id"] != context.user_id
            or record["channel_id"] != context.channel_id
        ):
            raise ScheduleChangeConflict("変更依頼の対象が一致しません。")

        # 完了済みの操作を再試行しても、新しい入力や予定には触れない。
        if record["status"] == "completed":
            return record
        if record["status"] != "pending":
            raise ScheduleChangeConflict("変更依頼の状態が不正です。")

        await controller._session()
        if await controller._admissions.get(context.session_id) is not None:
            raise ScheduleChangeConflict("参加承認済みのため変更できません。")

        reviews = await controller._kick_review._repository.for_session(
            context.session_id
        )
        if any(
            item["status"] in ("pending", "approved", "executing", "completed")
            for item in reviews
        ):
            raise ScheduleChangeConflict(
                "キック確認が進行しているため、日程を変更できません。"
            )

        binding = getattr(controller, "_no_show", None)
        if binding is not None:
            await binding.guard_input()

        archived_request = (
            await controller._repository.archived_for_reschedule(change_id)
        )
        archived_plan = (
            await controller._meetings.archived_for_schedule_change(change_id)
        )

        if archived_request is not None:
            self._check_request(archived_request, record)

        if archived_plan is not None:
            self._check_plan(archived_plan, record)
            if archived_request is None:
                raise ScheduleChangeConflict(
                    "変更前の希望日時の履歴が見つかりません。"
                )
            # 両方の履歴があるため、現行の予定には触れず完了にする。
        else:
            plan = await controller._plan()
            if record["proposal_id"] is None:
                if plan is not None:
                    raise ScheduleChangeConflict(
                        "面談予定が追加されています。最新の状態を確認してください。"
                    )
            else:
                self._check_plan(plan, record)

            if archived_request is None:
                request = await controller.load_request()
                self._check_request(request, record)
                await controller._repository.archive_for_reschedule(
                    request, proposal_id=change_id
                )

            if plan is not None:
                await controller._meetings.archive_for_schedule_change(
                    plan, change_id=change_id
                )

        controller._published_meeting = None
        return await self._repository.complete(change_id)
