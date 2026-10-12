"""通常の日程変更を、不参加による再調整とは別に保存する。"""

import re
from datetime import datetime, timezone
from uuid import uuid4

from wanbot.storage.async_store import AsyncGuildJsonStore


class ScheduleChangeConflict(ValueError):
    pass


def _key(value):
    return (
        type(value) is str
        and re.fullmatch(r"[0-9a-f]{32}", value) is not None
    )


def _id(value):
    return type(value) is int and 0 < value < 2**64


def _timestamp(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ScheduleChangeConflict("タイムゾーン付きの日時が必要です。")
    return value.astimezone(timezone.utc).isoformat()


def _read_timestamp(value):
    try:
        if type(value) is not str:
            raise ValueError
        result = datetime.fromisoformat(value)
        if result.utcoffset() is None:
            raise ValueError
        return result
    except (TypeError, ValueError):
        raise ScheduleChangeConflict("保存日時が不正です。") from None


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
        raise ScheduleChangeConflict("日程変更の保存形式が不正です。")

    fields = {
        "change_id", "guild_id", "session_id", "user_id", "channel_id",
        "requested_by", "source", "submission_at", "proposal_id",
        "created_at", "status",
    }
    for change_id, record in data["records"].items():
        if (
            not _key(change_id)
            or type(record) is not dict
            or set(record) != fields
            or record["change_id"] != change_id
            or not _key(record["session_id"])
            or any(
                not _id(record[name])
                for name in (
                    "guild_id", "user_id", "channel_id", "requested_by"
                )
            )
            or record["source"] not in ("member", "manager")
            or record["status"] not in ("pending", "completed")
            or (
                record["proposal_id"] is not None
                and not _key(record["proposal_id"])
            )
        ):
            raise ScheduleChangeConflict("日程変更の保存内容が不正です。")

        is_owner = record["requested_by"] == record["user_id"]
        if (record["source"] == "member") != is_owner:
            raise ScheduleChangeConflict("日程変更の依頼者が不正です。")

        submitted = _read_timestamp(record["submission_at"])
        created = _read_timestamp(record["created_at"])
        if created < submitted:
            raise ScheduleChangeConflict("変更依頼が提出日時より前です。")
    return data["records"]


class ScheduleChangeRepository:
    def __init__(self, config, guild_id, *, store=None):
        if not _id(guild_id):
            raise ScheduleChangeConflict("サーバーIDが不正です。")
        self._guild_id = guild_id
        self._store = (
            store if store is not None else
            AsyncGuildJsonStore(config, guild_id, "welcome_schedule_changes")
        )

    async def for_session(self, session_id):
        if not _key(session_id):
            raise ScheduleChangeConflict("Welcomeの識別子が不正です。")
        records = _records(await self._store.read())
        self._check_guild(records)
        return tuple(
            dict(record)
            for record in records.values()
            if record["session_id"] == session_id
        )

    def _check_guild(self, records):
        if any(
            record["guild_id"] != self._guild_id
            for record in records.values()
        ):
            raise ScheduleChangeConflict("保存先のサーバーが一致しません。")

    async def begin(
        self, *, context, request, proposal_id, actor_id, source, now
    ):
        """呼出元で本人・管理者権限を確認し、共通ロック内から呼ぶ。"""
        if (
            context.guild_id != self._guild_id
            or request.session_id != context.session_id
            or request.user_id != context.user_id
            or request.channel_id != context.channel_id
        ):
            raise ScheduleChangeConflict("変更対象のWelcomeが一致しません。")

        candidate = {
            "change_id": uuid4().hex,
            "guild_id": self._guild_id,
            "session_id": context.session_id,
            "user_id": context.user_id,
            "channel_id": context.channel_id,
            "requested_by": actor_id,
            "source": source,
            "submission_at": _timestamp(request.confirmed_at),
            "proposal_id": proposal_id,
            "created_at": _timestamp(now),
            "status": "pending",
        }
        _records({
            "version": 1,
            "records": {candidate["change_id"]: candidate},
        })
        result = None

        def change(data):
            nonlocal result
            records = dict(_records(data))
            self._check_guild(records)
            same_session = [
                item for item in records.values()
                if item["session_id"] == context.session_id
            ]
            identity_fields = (
                "guild_id", "session_id", "user_id", "channel_id",
                "submission_at", "proposal_id",
            )
            for previous in same_session:
                if all(
                    previous[name] == candidate[name]
                    for name in identity_fields
                ):
                    # 連打・再試行は同じ依頼を返す。
                    result = dict(previous)
                    return data

            if any(item["status"] == "pending" for item in same_session):
                raise ScheduleChangeConflict(
                    "別の日程変更が処理中です。少し待ってください。"
                )

            records[candidate["change_id"]] = candidate
            result = dict(candidate)
            return {"version": 1, "records": records}

        await self._store.update(change)
        return result

    async def complete(self, change_id):
        """希望日時・面談予定の切り替えが完了した後に呼ぶ。"""
        if not _key(change_id):
            raise ScheduleChangeConflict("日程変更の識別子が不正です。")
        result = None

        def change(data):
            nonlocal result
            records = dict(_records(data))
            self._check_guild(records)
            previous = records.get(change_id)
            if previous is None:
                raise ScheduleChangeConflict("日程変更が見つかりません。")
            result = dict(previous, status="completed")
            records[change_id] = result
            return {"version": 1, "records": records}

        await self._store.update(change)
        return dict(result)

    async def close(self):
        await self._store.close()
