"""本人待ちの開始・保留・リマインド済み状態を保存する。"""

import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from hashlib import sha256

from wanbot.storage.async_store import AsyncGuildJsonStore
from wanbot.welcome.waiting_limits import DEFAULT_LIMITS, WaitingLimits


class WaitingClockConflict(ValueError):
    pass


OWNERS = {"applicant", "staff", "scheduled", "excluded", "unknown"}
FIELDS = {
    "epoch", "owner", "reason", "progress_key", "started_at",
    "paused_at", "updated_at", "reminded",
}


def _key(value):
    if (
        type(value) is not str
        or re.fullmatch(r"[0-9a-f]{32}", value) is None
    ):
        raise WaitingClockConflict("案内・計測の識別子が不正です。")


def _label(value):
    if (
        type(value) is not str
        or re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", value) is None
    ):
        raise WaitingClockConflict("進行状態の識別子が不正です。")


def _utc(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise WaitingClockConflict("タイムゾーン付きの日時が必要です。")
    return value.astimezone(timezone.utc)


def _date(value):
    if type(value) is not str:
        raise WaitingClockConflict("保存日時が不正です。")
    try:
        return _utc(datetime.fromisoformat(value))
    except (TypeError, ValueError):
        raise WaitingClockConflict("保存日時が不正です。") from None


def _validate(record):
    if type(record) is not dict or set(record) != FIELDS:
        raise WaitingClockConflict("本人待ちの保存形式が不正です。")
    _key(record["epoch"])
    _label(record["reason"])
    _label(record["progress_key"])
    if record["owner"] not in OWNERS or type(record["reminded"]) is not bool:
        raise WaitingClockConflict("本人待ちの状態が不正です。")

    updated = _date(record["updated_at"])
    started = (
        None if record["started_at"] is None
        else _date(record["started_at"])
    )
    paused = (
        None if record["paused_at"] is None
        else _date(record["paused_at"])
    )
    if started is not None and started > updated:
        raise WaitingClockConflict("開始日時が更新日時より後になっています。")
    if paused is not None and (
        started is None or not started <= paused <= updated
    ):
        raise WaitingClockConflict("保留日時が不正です。")
    if record["owner"] == "applicant" and (
        started is None or paused is not None
    ):
        raise WaitingClockConflict("本人待ちの計測状態が不正です。")
    if record["owner"] == "unknown" and started is not None and paused is None:
        raise WaitingClockConflict("保留中の日時がありません。")
    if record["owner"] in {"staff", "scheduled", "excluded"} and (
        started is not None or paused is not None or record["reminded"]
    ):
        raise WaitingClockConflict("期限対象外の状態に計測が残っています。")
    if started is None and record["reminded"]:
        raise WaitingClockConflict("計測開始前の通知記録は保存できません。")


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
        raise WaitingClockConflict("本人待ちの保存形式が不正です。")
    for session_id, record in data["records"].items():
        _key(session_id)
        _validate(record)
    return data["records"]


def due_action(record, *, now, limits=DEFAULT_LIMITS):
    """通知候補を返すだけ。送信・承認依頼は別の処理で行う。"""
    if not isinstance(limits, WaitingLimits):
        raise WaitingClockConflict("本人期限の設定が不正です。")
    _validate(record)
    now = _utc(now)
    if now < _date(record["updated_at"]):
        raise WaitingClockConflict("現在時刻が保存済みの日時より前です。")
    if record["owner"] != "applicant":
        return None

    elapsed = now - _date(record["started_at"])
    if elapsed >= timedelta(seconds=limits.timeout_seconds):
        return "review"
    if elapsed >= timedelta(seconds=limits.reminder_seconds) and not record["reminded"]:
        return "remind"
    return None


class WaitingClockRepository:
    def __init__(
        self, config, guild_id, *, store=None, limits=DEFAULT_LIMITS
    ):
        if not isinstance(limits, WaitingLimits):
            raise WaitingClockConflict("本人期限の設定が不正です。")
        self.limits = limits
        self._store = (
            store if store is not None
            else AsyncGuildJsonStore(config, guild_id, "welcome_waiting_clocks")
        )

    async def get(self, session_id):
        _key(session_id)
        record = _records(await self._store.read()).get(session_id)
        return None if record is None else dict(record)

    async def observe(self, session_id, *, decision, progress_key, now):
        """共通ロック内で、取得できた現在の状態を保存する。

        progress_keyには回答本文を入れず、保存済みの更新番号などを使う。
        """
        _key(session_id)
        _label(progress_key)
        # 通常設定の既存記録は維持する。
        # 短縮設定では設定値も含めて進捗を識別し、変更時に計測を更新する。
        if self.limits != DEFAULT_LIMITS:
            progress_key = sha256(
                f"{self.limits.fingerprint}:{progress_key}".encode("utf-8")
            ).hexdigest()
        _label(decision.reason)
        if decision.owner not in OWNERS:
            raise WaitingClockConflict("待機状態が不正です。")
        now = _utc(now)
        result = None

        def change(data):
            nonlocal result
            records = _records(data)
            previous = records.get(session_id)
            if previous is not None and now < _date(previous["updated_at"]):
                raise WaitingClockConflict("更新日時が過去に戻っています。")

            record = (
                dict(previous) if previous is not None else {
                    "epoch": uuid4().hex,
                    "owner": "unknown",
                    "reason": decision.reason,
                    "progress_key": progress_key,
                    "started_at": None,
                    "paused_at": None,
                    "updated_at": now.isoformat(),
                    "reminded": False,
                }
            )

            if decision.owner == "unknown":
                # 状態不明のときは進捗更新を確定せず、元の識別子を保つ。
                if record["started_at"] is not None and record["paused_at"] is None:
                    record["paused_at"] = now.isoformat()
            elif decision.owner == "applicant":
                if (
                    record["started_at"] is None
                    or record["progress_key"] != progress_key
                ):
                    record.update(
                        epoch=uuid4().hex,
                        started_at=now.isoformat(),
                        paused_at=None,
                        reminded=False,
                    )
                elif record["paused_at"] is not None:
                    # 保留していた時間だけ、実効的な開始日時を後ろへずらす。
                    paused_at = _date(record["paused_at"])
                    started_at = _date(record["started_at"])
                    record["started_at"] = (
                        started_at + (now - paused_at)
                    ).isoformat()
                    record["paused_at"] = None
                record["progress_key"] = progress_key
            else:
                if record["started_at"] is not None:
                    record["epoch"] = uuid4().hex
                record.update(
                    progress_key=progress_key,
                    started_at=None,
                    paused_at=None,
                    reminded=False,
                )

            record.update(
                owner=decision.owner,
                reason=decision.reason,
                updated_at=now.isoformat(),
            )
            _validate(record)
            records[session_id] = record
            result = dict(record)
            return {"version": 1, "records": records}

        await self._store.update(change)
        return result

    async def mark_reminded(self, session_id, *, expected_epoch, now):
        """送信成功後に呼ぶ。古い計測に対する完了報告は受け付けない。"""
        _key(session_id)
        _key(expected_epoch)
        now = _utc(now)
        result = None

        def change(data):
            nonlocal result
            records = _records(data)
            record = records.get(session_id)
            if (
                record is None
                or record["epoch"] != expected_epoch
                or record["owner"] != "applicant"
            ):
                raise WaitingClockConflict("本人待ちの状態が変わっています。")
            if now < _date(record["updated_at"]):
                raise WaitingClockConflict("通知日時が過去に戻っています。")
            if now - _date(record["started_at"]) < timedelta(
                seconds=self.limits.reminder_seconds
            ):
                raise WaitingClockConflict("リマインドの時刻に達していません。")

            record = {
                **record, "reminded": True, "updated_at": now.isoformat()
            }
            _validate(record)
            records[session_id] = record
            result = dict(record)
            return {"version": 1, "records": records}

        await self._store.update(change)
        return result

    async def close(self):
        await self._store.close()
