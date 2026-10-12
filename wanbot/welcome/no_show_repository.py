"""面談不参加の回答期限と、一度だけの再調整を保存する。"""

import re
from datetime import datetime, timedelta, timezone

from wanbot.storage.async_store import AsyncGuildJsonStore


class NoShowConflict(ValueError):
    pass


STATUSES = {
    "waiting",
    "reschedule_requested",
    "second_confirmation",
    "kick_required",
}


def _key(value):
    return (
        isinstance(value, str)
        and re.fullmatch(r"[0-9a-f]{32}", value) is not None
    )


def _id(value):
    return type(value) is int and 0 < value < 2**64


def _utc(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise NoShowConflict("タイムゾーン付きの日時が必要です。")
    return value.astimezone(timezone.utc)


class NoShowRepository:
    def __init__(self, config, guild_id, *, store=None):
        self._guild_id = guild_id
        self._store = (
            store if store is not None else
            AsyncGuildJsonStore(config, guild_id, "welcome_no_shows")
        )

    def _records(self, data):
        if data == {}:
            return {}
        if (
            type(data) is not dict
            or set(data) != {"version", "records"}
            or type(data["version"]) is not int
            or data["version"] != 1
            or type(data["records"]) is not dict
        ):
            raise NoShowConflict("不参加記録の保存形式が不正です。")

        fields = {
            "proposal_id", "session_id", "guild_id", "user_id",
            "channel_id", "reported_by", "reported_at", "deadline",
            "status", "reason", "confirmed_by",
        }
        for key, record in data["records"].items():
            if (
                type(record) is not dict
                or set(record) != fields
                or not _key(key)
                or record["proposal_id"] != key
                or not _key(record["session_id"])
                or record["guild_id"] != self._guild_id
                or any(
                    not _id(record[name])
                    for name in (
                        "guild_id", "user_id", "channel_id", "reported_by"
                    )
                )
                or record["reported_by"] == record["user_id"]
                or record["status"] not in STATUSES
            ):
                raise NoShowConflict("不参加記録の内容が不正です。")

            try:
                reported = _utc(datetime.fromisoformat(record["reported_at"]))
                deadline = (
                    None if record["deadline"] is None
                    else _utc(datetime.fromisoformat(record["deadline"]))
                )
            except (TypeError, ValueError):
                raise NoShowConflict("不参加記録の日時が不正です。") from None

            if deadline is not None and deadline != reported + timedelta(hours=1):
                raise NoShowConflict("回答期限が不正です。")
            if record["status"] in ("waiting", "reschedule_requested"):
                if deadline is None or record["reason"] is not None:
                    raise NoShowConflict("回答待ちの記録が不正です。")
            if record["status"] == "second_confirmation":
                if deadline is not None or record["reason"] is not None:
                    raise NoShowConflict("2回目の確認記録が不正です。")
            if record["status"] == "kick_required":
                if record["reason"] not in (
                    "declined", "timeout", "second_no_show"
                ):
                    raise NoShowConflict("キック確認が必要な理由が不正です。")
                if record["reason"] == "second_no_show":
                    if (
                        deadline is not None
                        or not _id(record["confirmed_by"])
                        or record["confirmed_by"] == record["user_id"]
                    ):
                        raise NoShowConflict("管理者の最終確認が不正です。")
                elif deadline is None:
                    raise NoShowConflict("初回不参加の期限がありません。")

        return data["records"]

    async def get(self, proposal_id):
        record = self._records(await self._store.read()).get(proposal_id)
        return None if record is None else dict(record)

    async def all_records(self):
        return tuple(
            dict(record)
            for record in self._records(await self._store.read()).values()
        )

    async def begin(self, *, context, plan, actor_id, now):
        now = _utc(now)
        if (
            plan.status != "confirmed"
            or plan.guild_id != self._guild_id
            or context.guild_id != self._guild_id
            or plan.welcome_session_id != context.session_id
            or plan.user_id != context.user_id
            or plan.channel_id != context.channel_id
            or now < _utc(plan.meeting_at)
        ):
            raise NoShowConflict("開始時刻を過ぎた、現在の確定済み面談が必要です。")

        result = None

        def change(data):
            nonlocal result
            records = self._records(data)
            existing = records.get(plan.proposal_id)
            if existing is not None:
                if (
                    existing["session_id"] != context.session_id
                    or existing["user_id"] != context.user_id
                    or existing["channel_id"] != context.channel_id
                ):
                    raise NoShowConflict("不参加記録の対象が一致しません。")
                # 再クリックでは期限を変更しない。
                result = existing
                return data

            previous = [
                record for record in records.values()
                if record["session_id"] == context.session_id
            ]
            if any(
                record["status"] != "reschedule_requested"
                for record in previous
            ):
                raise NoShowConflict("別の不参加確認が進行中です。")

            second = bool(previous)
            record = {
                "proposal_id": plan.proposal_id,
                "session_id": context.session_id,
                "guild_id": self._guild_id,
                "user_id": context.user_id,
                "channel_id": context.channel_id,
                "reported_by": actor_id,
                "reported_at": now.isoformat(),
                "deadline": (
                    None if second else (now + timedelta(hours=1)).isoformat()
                ),
                "status": "second_confirmation" if second else "waiting",
                "reason": None,
                "confirmed_by": None,
            }
            records[plan.proposal_id] = record
            updated = {"version": 1, "records": records}
            self._records(updated)
            result = record
            return updated

        await self._store.update(change)
        return dict(result)

    async def _update(self, proposal_id, transform):
        result = None

        def change(data):
            nonlocal result
            records = self._records(data)
            current = records.get(proposal_id)
            if current is None:
                raise NoShowConflict("不参加記録が見つかりません。")
            result = transform(dict(current))
            records[proposal_id] = result
            updated = {"version": 1, "records": records}
            self._records(updated)
            return updated

        await self._store.update(change)
        return dict(result)

    async def respond(self, proposal_id, *, actor_id, reschedule, now):
        now = _utc(now)
        if type(reschedule) is not bool:
            raise NoShowConflict("YesまたはNoを選んでください。")

        def change(record):
            if actor_id != record["user_id"]:
                raise NoShowConflict("本人だけが回答できます。")
            if record["status"] != "waiting":
                raise NoShowConflict("この確認は回答待ちではありません。")
            if now < datetime.fromisoformat(record["reported_at"]):
                raise NoShowConflict("回答日時が不正です。")
            if now >= datetime.fromisoformat(record["deadline"]):
                return {**record, "status": "kick_required", "reason": "timeout"}
            if reschedule:
                return {**record, "status": "reschedule_requested"}
            return {**record, "status": "kick_required", "reason": "declined"}

        return await self._update(proposal_id, change)

    async def expire(self, proposal_id, *, now):
        now = _utc(now)

        def change(record):
            if (
                record["status"] == "waiting"
                and now >= datetime.fromisoformat(record["deadline"])
            ):
                return {**record, "status": "kick_required", "reason": "timeout"}
            return record

        return await self._update(proposal_id, change)

    async def confirm_second(self, proposal_id, *, actor_id):
        if not _id(actor_id):
            raise NoShowConflict("確認した管理者が不正です。")

        def change(record):
            if actor_id == record["user_id"]:
                raise NoShowConflict("管理者の確認が必要です。")
            if record["status"] != "second_confirmation":
                raise NoShowConflict("2回目の不参加確認待ちではありません。")
            return {
                **record,
                "status": "kick_required",
                "reason": "second_no_show",
                "confirmed_by": actor_id,
            }

        return await self._update(proposal_id, change)

    async def close(self):
        await self._store.close()
