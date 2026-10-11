from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from uuid import uuid4
import re

from wanbot.config import AppConfig
from wanbot.storage.async_store import AsyncGuildJsonStore


CLOSE_REASONS = frozenset({
    "member_left",
    "setup_cancelled",
    "completed",
    "kick_approved",
})


class WelcomeDataError(ValueError):
    """保存データが不正。データ本文はエラーに含めない。"""


class WelcomeConflict(ValueError):
    """現在の状態では要求された操作を実行できない。"""


def _check_id(value: int) -> None:
    if type(value) is not int or not 0 < value < 1 << 64:
        raise ValueError("Invalid Discord ID")


def _utc(value: datetime | None = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Timezone-aware datetime is required")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class WelcomeSession:
    session_id: str
    user_id: int
    channel_id: int | None
    state: str
    created_at: datetime
    updated_at: datetime
    closed_reason: str | None = None


def _encode(session: WelcomeSession) -> dict:
    data = asdict(session)
    data["created_at"] = session.created_at.isoformat()
    data["updated_at"] = session.updated_at.isoformat()
    return data


def _decode(data: dict) -> dict[str, WelcomeSession]:
    if data == {}:
        return {}

    try:
        if (
            set(data) != {"state_version", "sessions"}
            or type(data["state_version"]) is not int
            or data["state_version"] != 1
            or not isinstance(data["sessions"], dict)
        ):
            raise ValueError

        sessions = {}
        active_users = set()
        channels = set()
        fields = {
            "session_id",
            "user_id",
            "channel_id",
            "state",
            "created_at",
            "updated_at",
            "closed_reason",
        }

        for key, raw in data["sessions"].items():
            if (
                not isinstance(key, str)
                or re.fullmatch(r"[0-9a-f]{32}", key) is None
                or not isinstance(raw, dict)
                or set(raw) != fields
                or raw["session_id"] != key
            ):
                raise ValueError

            _check_id(raw["user_id"])

            channel_id = raw["channel_id"]
            if channel_id is not None:
                _check_id(channel_id)
                if channel_id in channels:
                    raise ValueError
                channels.add(channel_id)

            created_at = _utc(datetime.fromisoformat(raw["created_at"]))
            updated_at = _utc(datetime.fromisoformat(raw["updated_at"]))
            if updated_at < created_at:
                raise ValueError

            state = raw["state"]
            reason = raw["closed_reason"]

            if state not in (
                "preparing", "active", "archiving", "closed"
            ):
                raise ValueError

            if state in ("archiving", "closed"):
                if reason not in CLOSE_REASONS:
                    raise ValueError
            elif reason is not None:
                raise ValueError

            if state != "closed":
                if raw["user_id"] in active_users:
                    raise ValueError
                active_users.add(raw["user_id"])

            if state == "preparing" and channel_id is not None:
                raise ValueError
            if state == "active" and channel_id is None:
                raise ValueError

            sessions[key] = WelcomeSession(
                session_id=key,
                user_id=raw["user_id"],
                channel_id=channel_id,
                state=state,
                created_at=created_at,
                updated_at=updated_at,
                closed_reason=reason,
            )

        return sessions
    except (ValueError, TypeError, KeyError, OverflowError):
        raise WelcomeDataError("Invalid Welcome data") from None


def _document(sessions: dict[str, WelcomeSession]) -> dict:
    return {
        "state_version": 1,
        "sessions": {
            key: _encode(session)
            for key, session in sessions.items()
        },
    }


class WelcomeRepository:
    def __init__(self, config: AppConfig, guild_id: int) -> None:
        self._store = AsyncGuildJsonStore(config, guild_id, "welcome_sessions")

    async def begin(
        self,
        user_id: int,
        *,
        now: datetime | None = None,
    ) -> WelcomeSession:
        _check_id(user_id)
        timestamp = _utc(now)
        candidate = WelcomeSession(
            session_id=uuid4().hex,
            user_id=user_id,
            channel_id=None,
            state="preparing",
            created_at=timestamp,
            updated_at=timestamp,
        )
        result = None

        def change(data):
            nonlocal result
            sessions = _decode(data)

            for session in sessions.values():
                if session.user_id == user_id and session.state != "closed":
                    if session.state == "archiving":
                        raise WelcomeConflict(
                            "Previous Welcome archive is pending"
                        )
                    result = session
                    return _document(sessions)

            sessions[candidate.session_id] = candidate
            result = candidate
            return _document(sessions)

        await self._store.update(change)
        assert result is not None
        return result

    async def get_active(self, user_id: int) -> WelcomeSession | None:
        _check_id(user_id)
        sessions = _decode(await self._store.read())
        return next(
            (
                session
                for session in sessions.values()
                if session.user_id == user_id and session.state != "closed"
            ),
            None,
        )

    async def history(self, user_id: int) -> tuple[WelcomeSession, ...]:
        _check_id(user_id)
        sessions = _decode(await self._store.read())
        return tuple(sorted(
            (
                session
                for session in sessions.values()
                if session.user_id == user_id
            ),
            key=lambda session: (session.created_at, session.session_id),
        ))

    async def attach_channel(
        self,
        session_id: str,
        channel_id: int,
        *,
        now: datetime | None = None,
    ) -> WelcomeSession:
        _check_id(channel_id)
        timestamp = _utc(now)

        def change(session, sessions):
            if session.state == "active" and session.channel_id == channel_id:
                return session
            if session.state != "preparing":
                raise WelcomeConflict("Welcome is not preparing")
            if timestamp < session.updated_at:
                raise WelcomeConflict("Timestamp moved backwards")
            if any(item.channel_id == channel_id for item in sessions.values()):
                raise WelcomeConflict("Channel is already assigned")

            return replace(
                session,
                channel_id=channel_id,
                state="active",
                updated_at=timestamp,
            )

        return await self._update(session_id, change)

    async def begin_archive(
        self,
        session_id: str,
        *,
        reason: str,
        now: datetime | None = None,
    ) -> WelcomeSession:
        """Discordを変更する前に、ログ移動の予定を保存する。"""
        if reason not in CLOSE_REASONS:
            raise ValueError("Invalid close reason")
        timestamp = _utc(now)

        def change(session, sessions):
            if session.state in ("archiving", "closed"):
                if session.closed_reason != reason:
                    raise WelcomeConflict(
                        "Welcome close reason does not match"
                    )
                return session

            if timestamp < session.updated_at:
                raise WelcomeConflict("Timestamp moved backwards")

            return replace(
                session,
                state="archiving",
                closed_reason=reason,
                updated_at=timestamp,
            )

        return await self._update(session_id, change)

    async def record_archive_channel(
        self,
        session_id: str,
        channel_id: int,
        *,
        now: datetime | None = None,
    ) -> WelcomeSession:
        """ログ移動時に発見したチャンネルIDを履歴へ残す。"""
        _check_id(channel_id)
        timestamp = _utc(now)

        def change(session, sessions):
            if session.state != "archiving":
                raise WelcomeConflict("Welcome archive is not pending")
            if session.channel_id == channel_id:
                return session
            if session.channel_id is not None:
                raise WelcomeConflict("Welcome channel cannot be replaced")
            if timestamp < session.updated_at:
                raise WelcomeConflict("Timestamp moved backwards")
            if any(
                item.channel_id == channel_id
                for item in sessions.values()
            ):
                raise WelcomeConflict("Channel is already assigned")

            return replace(
                session,
                channel_id=channel_id,
                updated_at=timestamp,
            )

        return await self._update(session_id, change)

    async def pending_archives(self) -> tuple[WelcomeSession, ...]:
        """再起動後にも、未完了のログ移動を取得できる。"""
        sessions = _decode(await self._store.read())
        return tuple(sorted(
            (
                session
                for session in sessions.values()
                if session.state == "archiving"
            ),
            key=lambda session: (session.updated_at, session.session_id),
        ))

    async def close_session(
        self,
        session_id: str,
        *,
        reason: str,
        now: datetime | None = None,
    ) -> WelcomeSession:
        if reason not in CLOSE_REASONS:
            raise ValueError("Invalid close reason")
        timestamp = _utc(now)

        def change(session, sessions):
            if session.state == "closed":
                if session.closed_reason == reason:
                    return session
                raise WelcomeConflict("Welcome is already closed")
            if (
                session.state == "archiving"
                and session.closed_reason != reason
            ):
                raise WelcomeConflict(
                    "Welcome close reason does not match"
                )
            if timestamp < session.updated_at:
                raise WelcomeConflict("Timestamp moved backwards")

            return replace(
                session,
                state="closed",
                closed_reason=reason,
                updated_at=timestamp,
            )

        return await self._update(session_id, change)

    async def _update(self, session_id, transform) -> WelcomeSession:
        result = None

        def change(data):
            nonlocal result
            sessions = _decode(data)
            if session_id not in sessions:
                raise WelcomeConflict("Welcome session was not found")

            result = transform(sessions[session_id], sessions)
            sessions[session_id] = result
            return _document(sessions)

        await self._store.update(change)
        assert result is not None
        return result

    async def close(self) -> None:
        await self._store.close()
