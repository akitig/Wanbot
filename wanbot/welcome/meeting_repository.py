"""面談の日程・担当者・承諾状態をWelcomeごとに保存する。"""

import re
from dataclasses import asdict
from datetime import datetime

from wanbot.storage.async_store import AsyncGuildJsonStore
from wanbot.storage.json_store import StorageError
from wanbot.welcome.meeting_plan import (
    MeetingPlan,
    MeetingPlanConflict,
    propose_meeting,
    respond_to_meeting,
)


def _session_id(value):
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[0-9a-f]{32}", value) is None
    ):
        raise ValueError("Invalid session ID")


def _id(value):
    if type(value) is not int or not 0 < value < 2**64:
        raise ValueError("Invalid Discord ID")


def _encode(plan):
    record = asdict(plan)
    for key in ("meeting_at", "proposed_at", "decided_at"):
        value = record[key]
        record[key] = value.isoformat() if value is not None else None
    return record


def _restore(key, record, guild_id):
    try:
        _session_id(key)
        if type(record) is not dict:
            raise ValueError
        values = dict(record)
        for name in ("meeting_at", "proposed_at", "decided_at"):
            value = values[name]
            if name == "decided_at" and value is None:
                continue
            if type(value) is not str:
                raise ValueError
            parsed = datetime.fromisoformat(value)
            if parsed.utcoffset() is None:
                raise ValueError
            values[name] = parsed

        plan = MeetingPlan(**values)
        _session_id(plan.proposal_id)
        if plan.welcome_session_id != key or plan.guild_id != guild_id:
            raise ValueError

        for value in (
            plan.guild_id, plan.user_id, plan.channel_id,
            plan.first_manager_id,
        ):
            _id(value)

        if plan.first_manager_id == plan.user_id:
            raise ValueError
        if plan.second_manager_id is not None:
            _id(plan.second_manager_id)
            if plan.second_manager_id in (
                plan.first_manager_id, plan.user_id
            ):
                raise ValueError

        if type(plan.preference) is not int or plan.preference not in (1, 2, 3):
            raise ValueError
        if plan.meeting_at <= plan.proposed_at:
            raise ValueError
        if plan.status not in {"waiting_second", "confirmed", "declined"}:
            raise ValueError

        if plan.status == "waiting_second":
            if plan.second_manager_id is None or plan.decided_at is not None:
                raise ValueError
        else:
            if plan.decided_at is None or plan.decided_at < plan.proposed_at:
                raise ValueError
            if plan.status == "confirmed" and plan.decided_at >= plan.meeting_at:
                raise ValueError
            if plan.status == "declined" and plan.second_manager_id is None:
                raise ValueError

        if plan.second_manager_id is None and (
            plan.status != "confirmed"
            or plan.decided_at != plan.proposed_at
        ):
            raise ValueError

        return plan
    except (KeyError, TypeError, ValueError, OverflowError):
        raise StorageError("面談の保存データが不正です。") from None


class MeetingPlanRepository:
    def __init__(self, config, guild_id):
        self._config = config
        self._guild_id = guild_id
        self._store = AsyncGuildJsonStore(
            config, guild_id, "welcome_meeting_plans"
        )

    def _entries(self, data):
        if not data:
            return {}
        if (
            set(data) not in (
                {"version", "plans"},
                {"version", "plans", "reschedule_history"},
                {"version", "plans", "schedule_history"},
                {"version", "plans", "reschedule_history", "schedule_history"},
            )
            or type(data["version"]) is not int
            or data["version"] != 1
            or type(data["plans"]) is not dict
        ):
            raise StorageError("面談の保存形式が不正です。")

        self._reschedule_history(data)
        self._schedule_history(data)
        for key, record in data["plans"].items():
            _restore(key, record, self._guild_id)
        return data["plans"]

    async def get(self, session_id):
        _session_id(session_id)
        entries = self._entries(await self._store.read())
        record = entries.get(session_id)
        if record is None:
            return None
        return _restore(session_id, record, self._guild_id)

    async def propose(
        self,
        *,
        request,
        actor_id,
        actor_role_ids,
        preference,
        second_manager_id,
        second_manager_role_ids,
        now,
        expected_proposal_id=None,
    ):
        _session_id(request.session_id)
        if expected_proposal_id is not None:
            _session_id(expected_proposal_id)

        # 更新処理内でも使えるよう、ロール一覧を固定する。
        actor_roles = tuple(actor_role_ids)
        second_roles = tuple(second_manager_role_ids)
        saved = []

        def change(data):
            entries = self._entries(data)
            record = entries.get(request.session_id)
            previous = (
                _restore(request.session_id, record, self._guild_id)
                if record is not None else None
            )
            actual_id = previous.proposal_id if previous is not None else None
            if actual_id != expected_proposal_id:
                raise MeetingPlanConflict(
                    "面談の状態が変わりました。最新の画面から選び直してください。"
                )

            plan = propose_meeting(
                config=self._config,
                guild_id=self._guild_id,
                request=request,
                actor_id=actor_id,
                actor_role_ids=actor_roles,
                preference=preference,
                second_manager_id=second_manager_id,
                second_manager_role_ids=second_roles,
                now=now,
                previous=previous,
            )
            encoded = _encode(plan)
            _restore(request.session_id, encoded, self._guild_id)
            entries[request.session_id] = encoded
            saved.append(plan)
            return {
                "version": 1,
                "plans": entries,
                "reschedule_history": self._reschedule_history(data),
                "schedule_history": self._schedule_history(data),
            }

        await self._store.update(change)
        return saved[0]

    async def respond(
        self,
        *,
        session_id,
        proposal_id,
        actor_id,
        actor_role_ids,
        accepted,
        now,
    ):
        _session_id(session_id)
        _session_id(proposal_id)
        actor_roles = tuple(actor_role_ids)
        saved = []

        def change(data):
            entries = self._entries(data)
            record = entries.get(session_id)
            if record is None:
                raise MeetingPlanConflict("この面談の確認依頼は見つかりません。")

            current = _restore(session_id, record, self._guild_id)
            plan = respond_to_meeting(
                current,
                config=self._config,
                proposal_id=proposal_id,
                actor_id=actor_id,
                actor_role_ids=actor_roles,
                accepted=accepted,
                now=now,
            )
            encoded = _encode(plan)
            _restore(session_id, encoded, self._guild_id)
            entries[session_id] = encoded
            saved.append(plan)
            return {
                "version": 1,
                "plans": entries,
                "reschedule_history": self._reschedule_history(data),
                "schedule_history": self._schedule_history(data),
            }

        await self._store.update(change)
        return saved[0]


    def _reschedule_history(self, data):
        history = data.get("reschedule_history", {})
        if type(history) is not dict:
            raise StorageError("面談の再調整履歴が不正です。")

        for proposal_id, record in history.items():
            try:
                _session_id(proposal_id)
                if type(record) is not dict:
                    raise ValueError
                plan = _restore(
                    record["welcome_session_id"],
                    record,
                    self._guild_id,
                )
                if (
                    plan.proposal_id != proposal_id
                    or plan.status != "confirmed"
                ):
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                raise StorageError(
                    "面談の再調整履歴が不正です。"
                ) from None
        return history

    async def archive_for_reschedule(self, plan):
        """再調整が承認された後、Welcome共通ロック内で呼ぶ。"""
        encoded = _encode(plan)
        _restore(plan.welcome_session_id, encoded, self._guild_id)
        if plan.status != "confirmed":
            raise MeetingPlanConflict(
                "確定済みの面談だけを再調整履歴へ移せます。"
            )

        def change(data):
            entries = dict(self._entries(data))
            history = dict(self._reschedule_history(data))

            previous = history.get(plan.proposal_id)
            if previous is not None:
                archived = _restore(
                    plan.welcome_session_id, previous, self._guild_id
                )
                if archived != plan:
                    raise MeetingPlanConflict(
                        "この再調整には別の面談予定が記録されています。"
                    )
                # 処理済みなら、新しい面談予定には触れない。
                return data

            current = entries.get(plan.welcome_session_id)
            if (
                current is None
                or _restore(
                    plan.welcome_session_id, current, self._guild_id
                ) != plan
            ):
                raise MeetingPlanConflict(
                    "面談予定が更新されています。もう一度確認してください。"
                )

            history[plan.proposal_id] = encoded
            del entries[plan.welcome_session_id]
            return {
                "version": 1,
                "plans": entries,
                "reschedule_history": history,
                "schedule_history": self._schedule_history(data),
            }

        await self._store.update(change)

    async def archived_for_reschedule(self, proposal_id):
        """再調整前の確定日時・担当管理者を取得する。"""
        _session_id(proposal_id)
        data = await self._store.read()
        self._entries(data)
        record = self._reschedule_history(data).get(proposal_id)
        if record is None:
            return None
        return _restore(
            record["welcome_session_id"], record, self._guild_id
        )


    def _schedule_history(self, data):
        history = data.get("schedule_history", {})
        if type(history) is not dict:
            raise StorageError("通常の日程変更履歴が不正です。")
        for change_id, record in history.items():
            try:
                _session_id(change_id)
                if type(record) is not dict:
                    raise ValueError
                _restore(
                    record["welcome_session_id"], record, self._guild_id
                )
            except (KeyError, TypeError, ValueError):
                raise StorageError(
                    "通常の日程変更履歴が不正です。"
                ) from None
        return history

    async def archive_for_schedule_change(self, plan, *, change_id):
        """変更依頼を保存した後、Welcome共通ロック内で呼ぶ。"""
        _session_id(change_id)
        encoded = _encode(plan)
        _restore(plan.welcome_session_id, encoded, self._guild_id)

        def change(data):
            entries = dict(self._entries(data))
            history = dict(self._schedule_history(data))
            previous = history.get(change_id)
            if previous is not None:
                archived = _restore(
                    previous["welcome_session_id"],
                    previous,
                    self._guild_id,
                )
                if archived != plan:
                    raise MeetingPlanConflict(
                        "この変更には別の面談予定が記録されています。"
                    )
                # 再試行では、新しく決まった面談予定を消さない。
                return data

            current = entries.get(plan.welcome_session_id)
            if (
                current is None
                or _restore(
                    plan.welcome_session_id, current, self._guild_id
                ) != plan
            ):
                raise MeetingPlanConflict(
                    "面談予定が更新されています。最新の状態を確認してください。"
                )

            history[change_id] = encoded
            del entries[plan.welcome_session_id]
            return {
                "version": 1,
                "plans": entries,
                "reschedule_history": self._reschedule_history(data),
                "schedule_history": history,
            }

        await self._store.update(change)

    async def archived_for_schedule_change(self, change_id):
        _session_id(change_id)
        data = await self._store.read()
        self._entries(data)
        record = self._schedule_history(data).get(change_id)
        if record is None:
            return None
        return _restore(
            record["welcome_session_id"], record, self._guild_id
        )

    async def close(self):
        await self._store.close()
