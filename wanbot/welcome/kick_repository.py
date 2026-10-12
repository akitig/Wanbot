"""キック確認依頼と管理者の判断を保存する。実際のキックは行わない。"""

import re
import unicodedata
from datetime import datetime, timezone
from uuid import uuid4

from wanbot.storage.async_store import AsyncGuildJsonStore


class KickRequestConflict(ValueError):
    """対象や確認依頼の状態が変わっている。"""


STATES = {
    "pending", "approved", "executing",
    "completed", "cancelled", "obsolete",
}


def _id(value):
    return type(value) is int and 0 < value < 2**64


def _key(value):
    return (
        isinstance(value, str)
        and re.fullmatch(r"[0-9a-f]{32}", value) is not None
    )


def _timestamp(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise KickRequestConflict("タイムゾーン付きの日時が必要です。")
    return value.astimezone(timezone.utc).isoformat()


def normalize_reason(value):
    if not isinstance(value, str):
        raise KickRequestConflict("不承認理由を入力してください。")
    value = value.strip()
    if not value or len(value) > 500:
        raise KickRequestConflict("不承認理由は1〜500文字で入力してください。")
    if not any(
        not char.isspace()
        and not unicodedata.category(char).startswith("C")
        for char in value
    ):
        raise KickRequestConflict("読める文字で理由を入力してください。")
    if any(
        unicodedata.category(char) == "Cc" and char not in "\n\t"
        for char in value
    ):
        raise KickRequestConflict("理由に使用できない文字が含まれています。")
    return value


def same_membership(record, *, guild_id, user_id, session_id, joined_at):
    """現在も、確認依頼を作ったときと同じ参加であるか。"""
    if joined_at is None:
        return False
    return (
        record["guild_id"] == guild_id
        and record["user_id"] == user_id
        and record["session_id"] == session_id
        and record["joined_at"] == _timestamp(joined_at)
    )



def _validate_waiting_timeout(value):
    if (
        type(value) is not dict
        or set(value) != {"epoch", "progress_key"}
        or not _key(value["epoch"])
        or type(value["progress_key"]) is not str
        or re.fullmatch(
            r"[A-Za-z0-9:_-]{1,128}", value["progress_key"]
        ) is None
    ):
        raise KickRequestConflict("本人期限の識別情報が不正です。")


class KickRequestRepository:
    def __init__(self, config, guild_id, *, store=None):
        self._guild_id = guild_id
        self._store = (
            store if store is not None else
            AsyncGuildJsonStore(config, guild_id, "welcome_kick_requests")
        )

    def _records(self, data):
        if data == {}:
            return {}
        if (
            type(data) is not dict
            or set(data) != {"version", "requests"}
            or type(data["version"]) is not int
            or data["version"] != 1
            or type(data["requests"]) is not dict
        ):
            raise KickRequestConflict("キック確認依頼の保存形式が不正です。")

        fields = {
            "request_id", "guild_id", "session_id", "user_id",
            "channel_id", "joined_at", "reason", "requested_by",
            "requested_at", "status", "decided_by", "decided_at",
        }
        for key, record in data["requests"].items():
            if (
                type(record) is not dict
                or set(record) not in (
                    fields, fields | {"waiting_timeout"},
                    fields | {"age_review"},
                )
                or not _key(key)
                or record["request_id"] != key
                or not _key(record["session_id"])
                or record["guild_id"] != self._guild_id
                or any(
                    not _id(record[name])
                    for name in (
                        "guild_id", "user_id", "channel_id", "requested_by"
                    )
                )
                or record["requested_by"] == record["user_id"]
                or record["status"] not in STATES
            ):
                raise KickRequestConflict("キック確認依頼の内容が不正です。")
            if "waiting_timeout" in record:
                _validate_waiting_timeout(record["waiting_timeout"])
            if "age_review" in record and record["age_review"] is not True:
                raise KickRequestConflict("年齢確認の識別情報が不正です。")
            if normalize_reason(record["reason"]) != record["reason"]:
                raise KickRequestConflict("保存された理由が不正です。")
            try:
                for name in ("joined_at", "requested_at"):
                    parsed = datetime.fromisoformat(record[name])
                    if _timestamp(parsed) != record[name]:
                        raise ValueError
                if record["decided_by"] is not None:
                    if (
                        not _id(record["decided_by"])
                        or record["decided_by"] == record["user_id"]
                    ):
                        raise ValueError
                    decided = datetime.fromisoformat(record["decided_at"])
                    if _timestamp(decided) != record["decided_at"]:
                        raise ValueError
                elif record["decided_at"] is not None:
                    raise ValueError
            except (TypeError, ValueError):
                raise KickRequestConflict("判断者または日時が不正です。") from None
            if record["status"] in {"approved", "executing", "completed", "cancelled"}:
                if record["decided_by"] is None:
                    raise KickRequestConflict("管理者の判断が記録されていません。")
            if record["status"] == "pending" and record["decided_by"] is not None:
                raise KickRequestConflict("判断待ちの記録が不正です。")

        return data["requests"]

    async def get(self, request_id):
        record = self._records(await self._store.read()).get(request_id)
        return None if record is None else dict(record)

    async def for_session(self, session_id):
        return tuple(
            dict(record)
            for record in self._records(await self._store.read()).values()
            if record["session_id"] == session_id
        )

    async def create(
        self, *, context, joined_at, actor_id, reason, now=None,
        waiting_timeout=None, age_review=False
    ):
        if type(age_review) is not bool:
            raise KickRequestConflict("年齢確認の指定が不正です。")
        if age_review and waiting_timeout is not None:
            raise KickRequestConflict("年齢確認と期限切れは別の依頼にしてください。")
        candidate = {
            "request_id": uuid4().hex,
            "guild_id": context.guild_id,
            "session_id": context.session_id,
            "user_id": context.user_id,
            "channel_id": context.channel_id,
            "joined_at": _timestamp(joined_at),
            "reason": normalize_reason(reason),
            "requested_by": actor_id,
            "requested_at": _timestamp(now or datetime.now(timezone.utc)),
            "status": "pending",
            "decided_by": None,
            "decided_at": None,
        }
        if age_review:
            candidate["age_review"] = True
        if waiting_timeout is not None:
            _validate_waiting_timeout(waiting_timeout)
            candidate["waiting_timeout"] = dict(waiting_timeout)
        self._records({
            "version": 1,
            "requests": {candidate["request_id"]: candidate},
        })
        result = None

        def change(data):
            nonlocal result
            records = self._records(data)
            for previous in records.values():
                if (
                    previous["session_id"] == context.session_id
                    and previous["status"] in {
                        "pending", "approved", "executing", "completed"
                    }
                ):
                    raise KickRequestConflict(
                        "このWelcomeには、すでにキック確認依頼があります。"
                    )
            records[candidate["request_id"]] = candidate
            result = dict(candidate)
            return {"version": 1, "requests": records}

        await self._store.update(change)
        return result

    async def decide(self, request_id, *, actor_id, approve, now=None):
        if not _id(actor_id) or type(approve) is not bool:
            raise KickRequestConflict("管理者の判断が不正です。")
        timestamp = _timestamp(now or datetime.now(timezone.utc))
        result = None

        def change(data):
            nonlocal result
            records = self._records(data)
            previous = records.get(request_id)
            if previous is None or previous["status"] != "pending":
                raise KickRequestConflict("この確認依頼は判断待ちではありません。")
            if actor_id == previous["user_id"]:
                raise KickRequestConflict("対象者本人は承認できません。")
            if timestamp < previous["requested_at"]:
                raise KickRequestConflict("判断日時が依頼日時より前です。")
            result = {
                **previous,
                "status": "approved" if approve else "cancelled",
                "decided_by": actor_id,
                "decided_at": timestamp,
            }
            records[request_id] = result
            return {"version": 1, "requests": records}

        await self._store.update(change)
        return dict(result)

    async def all_reviews(self):
        return tuple(
            dict(record)
            for record in self._records(await self._store.read()).values()
        )

    async def transition(self, request_id, *, expected, target):
        allowed = {
            ("approved", "executing"),
            ("executing", "completed"),
            ("pending", "obsolete"),
            ("approved", "obsolete"),
            ("executing", "obsolete"),
            ("approved", "cancelled"),
        }
        if (expected, target) not in allowed:
            raise KickRequestConflict("許可されていない状態変更です。")
        result = None

        def change(data):
            nonlocal result
            records = self._records(data)
            current = records.get(request_id)
            if current is None or current["status"] != expected:
                raise KickRequestConflict("確認依頼の状態が変わっています。")
            result = {**current, "status": target}
            records[request_id] = result
            return {"version": 1, "requests": records}

        await self._store.update(change)
        return dict(result)

    async def pending_reviews(self):
        return tuple(
            dict(record)
            for record in self._records(await self._store.read()).values()
            if record["status"] == "pending"
        )

    async def close(self):
        await self._store.close()
