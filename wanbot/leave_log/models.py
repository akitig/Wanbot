"""退出を検知した時点の情報。取得不能と空の情報を区別する。"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone


class DepartureDataError(ValueError):
    pass


def _id(value):
    if type(value) is not int or not 0 < value < 2**64:
        raise DepartureDataError("Discord IDが不正です。")


def _text(value):
    if type(value) is not str or not value.strip():
        raise DepartureDataError("名前が不正です。")


def _time(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise DepartureDataError("タイムゾーン付きの日時が必要です。")


@dataclass(frozen=True)
class RoleSnapshot:
    role_id: int
    name: str

    def __post_init__(self):
        _id(self.role_id)
        _text(self.name)


@dataclass(frozen=True)
class DepartureSnapshot:
    event_id: str
    guild_id: int
    user_id: int
    username: str
    display_name: str | None
    roles: tuple[RoleSnapshot, ...] | None
    joined_at: datetime | None
    observed_at: datetime

    def __post_init__(self):
        if (
            type(self.event_id) is not str
            or re.fullmatch(r"[0-9a-f]{32}", self.event_id) is None
        ):
            raise DepartureDataError("退出記録の識別子が不正です。")
        _id(self.guild_id)
        _id(self.user_id)
        _text(self.username)
        if self.display_name is not None:
            _text(self.display_name)

        if self.roles is not None:
            if (
                type(self.roles) is not tuple
                or any(not isinstance(role, RoleSnapshot) for role in self.roles)
            ):
                raise DepartureDataError("ロールの情報が不正です。")
            ids = [role.role_id for role in self.roles]
            if len(ids) != len(set(ids)):
                raise DepartureDataError("ロールが重複しています。")

        _time(self.observed_at)
        if self.joined_at is not None:
            _time(self.joined_at)
            if self.joined_at > self.observed_at:
                raise DepartureDataError("参加日時が退出検知日時より後です。")


def encode_snapshot(snapshot):
    def timestamp(value):
        return value.astimezone(timezone.utc).isoformat()

    return {
        "event_id": snapshot.event_id,
        "guild_id": snapshot.guild_id,
        "user_id": snapshot.user_id,
        "username": snapshot.username,
        "display_name": snapshot.display_name,
        "roles": (
            None if snapshot.roles is None else [
                {"role_id": role.role_id, "name": role.name}
                for role in snapshot.roles
            ]
        ),
        "joined_at": (
            None if snapshot.joined_at is None
            else timestamp(snapshot.joined_at)
        ),
        "observed_at": timestamp(snapshot.observed_at),
    }


def decode_snapshot(record):
    fields = {
        "event_id", "guild_id", "user_id", "username", "display_name",
        "roles", "joined_at", "observed_at",
    }
    if type(record) is not dict or set(record) != fields:
        raise DepartureDataError("退出情報の保存形式が不正です。")

    try:
        roles = record["roles"]
        if roles is not None:
            if type(roles) is not list:
                raise ValueError
            restored = []
            for role in roles:
                if type(role) is not dict or set(role) != {"role_id", "name"}:
                    raise ValueError
                restored.append(RoleSnapshot(role["role_id"], role["name"]))
            roles = tuple(restored)

        def parse(value):
            if type(value) is not str:
                raise ValueError
            result = datetime.fromisoformat(value)
            _time(result)
            return result

        return DepartureSnapshot(
            event_id=record["event_id"],
            guild_id=record["guild_id"],
            user_id=record["user_id"],
            username=record["username"],
            display_name=record["display_name"],
            roles=roles,
            joined_at=(
                None if record["joined_at"] is None
                else parse(record["joined_at"])
            ),
            observed_at=parse(record["observed_at"]),
        )
    except (TypeError, ValueError, OverflowError):
        raise DepartureDataError("退出情報の保存内容が不正です。") from None
