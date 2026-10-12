"""決定済みの面談希望日時を、Welcomeの案内ごとに保存する。"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from wanbot.storage.async_store import AsyncGuildJsonStore
from wanbot.storage.json_store import StorageError
from wanbot.welcome.interview_dates import DATE_FORMAT, validate_dates


class InterviewRequestConflict(ValueError):
    """希望日時が決定済み、または対象の案内が不正。"""


@dataclass(frozen=True)
class InterviewRequest:
    session_id: str
    user_id: int
    channel_id: int
    preferences: tuple[datetime, datetime, datetime]
    confirmed_at: datetime


def _session_id(value):
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[0-9a-f]{32}", value) is None
    ):
        raise ValueError("Invalid Welcome session ID")


def _discord_id(value):
    if type(value) is not int or not 0 < value < 2**64:
        raise ValueError("Invalid Discord ID")


def _restore(session_id, record):
    try:
        _session_id(session_id)
        if type(record) is not dict or set(record) != {
            "user_id", "channel_id", "preferences", "confirmed_at"
        }:
            raise ValueError

        _discord_id(record["user_id"])
        _discord_id(record["channel_id"])

        if type(record["confirmed_at"]) is not str:
            raise ValueError
        confirmed_at = datetime.fromisoformat(record["confirmed_at"])
        if confirmed_at.utcoffset() is None:
            raise ValueError

        raw_preferences = record["preferences"]
        if type(raw_preferences) is not list:
            raise ValueError

        # 読み込み時点ではなく、決定した時点で有効だったかを検証する。
        preferences = validate_dates(
            raw_preferences, now=confirmed_at
        )
        if raw_preferences != [
            value.strftime(DATE_FORMAT) for value in preferences
        ]:
            raise ValueError

        return InterviewRequest(
            session_id=session_id,
            user_id=record["user_id"],
            channel_id=record["channel_id"],
            preferences=preferences,
            confirmed_at=confirmed_at,
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        raise StorageError("面談希望日時の保存データが不正です。") from None



def _reschedule_history(data):
    history = data.get("reschedule_history", {})
    if type(history) is not dict:
        raise StorageError("面談希望日時の再調整履歴が不正です。")

    for proposal_id, entry in history.items():
        try:
            _session_id(proposal_id)
            if type(entry) is not dict or set(entry) != {
                "session_id", "request"
            }:
                raise ValueError
            _restore(entry["session_id"], entry["request"])
        except (KeyError, TypeError, ValueError):
            raise StorageError(
                "面談希望日時の再調整履歴が不正です。"
            ) from None
    return history

def _entries(data):
    if not data:
        return {}
    if (
        set(data) not in (
            {"version", "requests"},
            {"version", "requests", "reschedule_history"},
        )
        or type(data["version"]) is not int
        or data["version"] != 1
        or type(data["requests"]) is not dict
    ):
        raise StorageError("面談希望日時の保存形式が不正です。")

    _reschedule_history(data)
    entries = data["requests"]
    for session_id, record in entries.items():
        _restore(session_id, record)
    return entries


class InterviewRequestRepository:
    def __init__(self, config, guild_id):
        self._store = AsyncGuildJsonStore(
            config, guild_id, "welcome_interview_requests"
        )

    async def get(self, session_id):
        _session_id(session_id)
        data = await self._store.read()
        record = _entries(data).get(session_id)
        if record is None:
            return None
        return _restore(session_id, record)

    async def confirm(self, session, preferences, *, now):
        """条件確認後、Welcome共通ロック内から呼ぶ。"""
        if session.state != "active":
            raise InterviewRequestConflict(
                "このWelcomeでは希望日時を確定できません。"
            )

        _session_id(session.session_id)
        _discord_id(session.user_id)
        _discord_id(session.channel_id)
        dates = validate_dates(preferences, now=now)

        record = {
            "user_id": session.user_id,
            "channel_id": session.channel_id,
            "preferences": [
                value.strftime(DATE_FORMAT) for value in dates
            ],
            "confirmed_at": now.astimezone(timezone.utc).isoformat(),
        }

        def change(data):
            entries = _entries(data)
            if session.session_id in entries:
                raise InterviewRequestConflict(
                    "面談希望日時はすでに決定されています。"
                    "変更が必要な場合は運営にお知らせください。"
                )
            entries[session.session_id] = record
            return {
                "version": 1,
                "requests": entries,
                "reschedule_history": _reschedule_history(data),
            }

        # 確認と書き込みをまとめ、連続クリックでも上書きしない。
        await self._store.update(change)
        return _restore(session.session_id, record)


    async def archive_for_reschedule(self, request, *, proposal_id):
        """Welcome共通ロック内で、再調整が承認された後に呼ぶ。"""
        _session_id(proposal_id)
        _session_id(request.session_id)

        def change(data):
            entries = dict(_entries(data))
            history = dict(_reschedule_history(data))

            previous = history.get(proposal_id)
            if previous is not None:
                archived = _restore(
                    previous["session_id"], previous["request"]
                )
                if archived != request:
                    raise InterviewRequestConflict(
                        "この再調整には別の希望日時が記録されています。"
                    )
                # 処理済みなら、新たな希望日時には触れない。
                return data

            current = entries.get(request.session_id)
            if (
                current is None
                or _restore(request.session_id, current) != request
            ):
                raise InterviewRequestConflict(
                    "希望日時が更新されています。もう一度確認してください。"
                )

            history[proposal_id] = {
                "session_id": request.session_id,
                "request": dict(current),
            }
            del entries[request.session_id]
            return {
                "version": 1,
                "requests": entries,
                "reschedule_history": history,
            }

        await self._store.update(change)

    async def archived_for_reschedule(self, proposal_id):
        """再調整前の希望日時を取得する。"""
        _session_id(proposal_id)
        data = await self._store.read()
        _entries(data)
        entry = _reschedule_history(data).get(proposal_id)
        if entry is None:
            return None
        return _restore(entry["session_id"], entry["request"])

    async def close(self):
        await self._store.close()
