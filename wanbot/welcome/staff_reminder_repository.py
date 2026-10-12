"""運営への催促時刻を保存する。Discordへの送信は行わない。"""

import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from wanbot.storage.async_store import AsyncGuildJsonStore


class StaffReminderConflict(ValueError):
    pass


INTERVAL = timedelta(hours=1)


def _key(value):
    return (
        type(value) is str
        and re.fullmatch(r"[0-9a-f]{32}", value) is not None
    )


def _target(value):
    if (
        type(value) is not str
        or re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", value) is None
    ):
        raise StaffReminderConflict("催促対象の識別子が不正です。")


def _utc(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise StaffReminderConflict("タイムゾーン付きの日時が必要です。")
    return value.astimezone(timezone.utc)


def _date(value):
    try:
        if type(value) is not str:
            raise ValueError
        return _utc(datetime.fromisoformat(value))
    except (TypeError, ValueError):
        raise StaffReminderConflict("催促の保存日時が不正です。") from None


def _records(data):
    if data == {}:
        return {}
    if (
        type(data) is not dict
        or set(data) != {"version", "records"}
        or type(data["version"]) is not int
        or data["version"] != 1
        or type(data["records"]) is not dict
    ):
        raise StaffReminderConflict("催促の保存形式が不正です。")

    fields = {
        "session_id", "channel_id", "active", "next_at",
        "updated_at", "attempt_id", "sent_id",
    }
    for target, record in data["records"].items():
        _target(target)
        if (
            type(record) is not dict
            or set(record) != fields
            or not _key(record["session_id"])
            or type(record["channel_id"]) is not int
            or not 0 < record["channel_id"] < 2**64
            or type(record["active"]) is not bool
        ):
            raise StaffReminderConflict("催促の保存内容が不正です。")
        _date(record["next_at"])
        _date(record["updated_at"])
        for name in ("attempt_id", "sent_id"):
            if record[name] is not None and not _key(record[name]):
                raise StaffReminderConflict("催促の送信記録が不正です。")
        if record["sent_id"] is not None and record["attempt_id"] is None:
            raise StaffReminderConflict("催促の送信記録が不正です。")
    return data["records"]


class StaffReminderRepository:
    def __init__(self, config, guild_id, *, store=None):
        self._store = (
            store if store is not None else
            AsyncGuildJsonStore(config, guild_id, "welcome_staff_reminders")
        )

    async def get(self, target):
        _target(target)
        record = _records(await self._store.read()).get(target)
        return None if record is None else dict(record)

    async def ensure(self, target, *, session_id, channel_id, now):
        _target(target)
        now = _utc(now)
        candidate = {
            "session_id": session_id,
            "channel_id": channel_id,
            "active": True,
            "next_at": (now + INTERVAL).isoformat(),
            "updated_at": now.isoformat(),
            "attempt_id": None,
            "sent_id": None,
        }
        _records({"version": 1, "records": {target: candidate}})
        result = None

        def change(data):
            nonlocal result
            records = _records(data)
            previous = records.get(target)
            if previous is not None:
                if (
                    previous["session_id"] != session_id
                    or previous["channel_id"] != channel_id
                ):
                    raise StaffReminderConflict("催促対象が一致しません。")
                result = dict(previous)
                return data
            records[target] = candidate
            result = dict(candidate)
            return {"version": 1, "records": records}

        await self._store.update(change)
        return result

    async def _update(self, target, *, now, transform):
        _target(target)
        now = _utc(now)
        result = None

        def change(data):
            nonlocal result
            records = _records(data)
            previous = records.get(target)
            if previous is None:
                raise StaffReminderConflict("催促対象が見つかりません。")
            if now < _date(previous["updated_at"]):
                raise StaffReminderConflict("催促の更新日時が過去に戻っています。")
            record, result = transform(dict(previous), now)
            records[target] = record
            updated = {"version": 1, "records": records}
            _records(updated)
            return updated

        await self._store.update(change)
        return result

    async def claim(self, target, *, now):
        """送信直前に呼ぶ。送信できる場合だけ試行IDを返す。"""
        def transform(record, timestamp):
            if not record["active"] or timestamp < _date(record["next_at"]):
                return record, None
            attempt_id = uuid4().hex
            record.update(
                attempt_id=attempt_id,
                next_at=(timestamp + INTERVAL).isoformat(),
                updated_at=timestamp.isoformat(),
            )
            return record, attempt_id

        return await self._update(target, now=now, transform=transform)

    async def mark_sent(self, target, *, attempt_id, now):
        if not _key(attempt_id):
            raise StaffReminderConflict("送信試行の識別子が不正です。")

        def transform(record, timestamp):
            if not record["active"] or record["attempt_id"] != attempt_id:
                raise StaffReminderConflict("催促の送信状態が変わっています。")
            record.update(
                sent_id=attempt_id,
                updated_at=timestamp.isoformat(),
            )
            return record, None

        await self._update(target, now=now, transform=transform)

    async def finish(self, target, *, now):
        def transform(record, timestamp):
            record.update(active=False, updated_at=timestamp.isoformat())
            return record, None

        await self._update(target, now=now, transform=transform)

    async def close(self):
        await self._store.close()
