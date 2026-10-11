"""BUMPパネルと待機時刻をサーバー別に保存する。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any

from wanbot.config import AppConfig
from wanbot.storage.async_store import AsyncGuildJsonStore
from wanbot.storage.json_store import StorageError


def _check_id(value: int) -> None:
    if type(value) is not int or not 0 < value < 2**64:
        raise ValueError("有効なDiscord IDを指定してください。")


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("日時にはタイムゾーンを指定してください。")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class BumpState:
    panel_message_id: int | None = None
    last_success_message_id: int | None = None
    last_bumped_at: datetime | None = None
    next_bump_at: datetime | None = None

    def is_available(self, now: datetime) -> bool:
        current = _utc(now)
        return self.next_bump_at is None or current >= self.next_bump_at


def _encode(state: BumpState) -> dict[str, Any]:
    return {
        "state_version": 1,
        "panel_message_id": state.panel_message_id,
        "last_success_message_id": state.last_success_message_id,
        "last_bumped_at": (
            state.last_bumped_at.isoformat()
            if state.last_bumped_at is not None
            else None
        ),
        "next_bump_at": (
            state.next_bump_at.isoformat()
            if state.next_bump_at is not None
            else None
        ),
    }


def _decode(data: dict[str, Any]) -> BumpState:
    if not data:
        return BumpState()

    try:
        if set(data) != {
            "state_version",
            "panel_message_id",
            "last_success_message_id",
            "last_bumped_at",
            "next_bump_at",
        }:
            raise ValueError

        if type(data["state_version"]) is not int or data["state_version"] != 1:
            raise ValueError

        panel_id = data["panel_message_id"]
        success_id = data["last_success_message_id"]

        for value in (panel_id, success_id):
            if value is not None:
                _check_id(value)

        last_raw = data["last_bumped_at"]
        next_raw = data["next_bump_at"]

        if success_id is None:
            if last_raw is not None or next_raw is not None:
                raise ValueError
            last_at = None
            next_at = None
        else:
            if type(last_raw) is not str or type(next_raw) is not str:
                raise ValueError
            last_at = _utc(datetime.fromisoformat(last_raw))
            next_at = _utc(datetime.fromisoformat(next_raw))
            if next_at <= last_at:
                raise ValueError

        return BumpState(
            panel_message_id=panel_id,
            last_success_message_id=success_id,
            last_bumped_at=last_at,
            next_bump_at=next_at,
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        raise StorageError(
            "BUMP保存データの形式が不正です。元のデータは変更していません。"
        ) from None


class BumpRepository:
    def __init__(self, config: AppConfig, guild_id: int) -> None:
        self._store = AsyncGuildJsonStore(config, guild_id, "bump_state")

    async def read(self) -> BumpState:
        return _decode(await self._store.read())

    async def set_panel_message_id(self, message_id: int | None) -> None:
        if message_id is not None:
            _check_id(message_id)

        def change(data: dict[str, Any]) -> dict[str, Any]:
            current = _decode(data)
            return _encode(replace(current, panel_message_id=message_id))

        await self._store.update(change)

    async def record_success(
        self,
        *,
        message_id: int,
        occurred_at: datetime,
        cooldown_seconds: int = 7200,
    ) -> bool:
        """新しい成功投稿だけを保存する。重複・古い投稿ならFalseを返す。"""
        _check_id(message_id)
        timestamp = _utc(occurred_at)

        if (
            type(cooldown_seconds) is not int
            or not 1 <= cooldown_seconds <= 2_147_483_647
        ):
            raise ValueError("待機時間は有効な正の整数の秒数で指定してください。")

        next_at = timestamp + timedelta(seconds=cooldown_seconds)
        accepted = False

        def change(data: dict[str, Any]) -> dict[str, Any]:
            nonlocal accepted
            current = _decode(data)

            # DiscordのメッセージIDと成功日時の両方で巻き戻りを防ぐ。
            if current.last_success_message_id is not None:
                if message_id <= current.last_success_message_id:
                    return data
                if timestamp < current.last_bumped_at:
                    return data

            updated = replace(
                current,
                last_success_message_id=message_id,
                last_bumped_at=timestamp,
                next_bump_at=next_at,
            )
            accepted = True
            return _encode(updated)

        await self._store.update(change)
        return accepted

    async def close(self) -> None:
        await self._store.close()
