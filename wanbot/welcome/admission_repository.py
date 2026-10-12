"""参加承認と、ロール付与・Log移動の進行状態を保存する。"""

import re
from datetime import datetime, timezone

from wanbot.storage.async_store import AsyncGuildJsonStore


PHASES = ("approved", "roles_verified", "completed")


class AdmissionConflict(ValueError):
    """別の承認や、不正な順序での更新を防ぐ。"""


def _id(value):
    return type(value) is int and 0 < value < 2**64


def _session_id(value):
    return (
        isinstance(value, str)
        and re.fullmatch(r"[0-9a-f]{32}", value) is not None
    )


def _validate_record(record, guild_id):
    fields = {
        "session_id", "proposal_id", "guild_id", "user_id",
        "channel_id", "approved_by", "role_ids", "approved_at", "phase",
    }
    if type(record) is not dict or set(record) != fields:
        raise AdmissionConflict("参加承認の保存形式が不正です。")
    if (
        not _session_id(record["session_id"])
        or not _session_id(record["proposal_id"])
        or record["guild_id"] != guild_id
        or any(
            not _id(record[key])
            for key in ("guild_id", "user_id", "channel_id", "approved_by")
        )
        or record["approved_by"] == record["user_id"]
        or record["phase"] not in PHASES
    ):
        raise AdmissionConflict("参加承認の対象が不正です。")

    roles = record["role_ids"]
    if (
        type(roles) is not list
        or not roles
        or any(not _id(value) for value in roles)
        or len(set(roles)) != len(roles)
    ):
        raise AdmissionConflict("参加承認のロール指定が不正です。")

    try:
        timestamp = datetime.fromisoformat(record["approved_at"])
        if timestamp.utcoffset() is None:
            raise ValueError
    except (TypeError, ValueError):
        raise AdmissionConflict("参加承認の日時が不正です。") from None


class AdmissionRepository:
    def __init__(self, config, guild_id, *, store=None):
        self._guild_id = guild_id
        self._store = (
            store if store is not None
            else AsyncGuildJsonStore(config, guild_id, "welcome_admissions")
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
            raise AdmissionConflict("参加承認の保存形式が不正です。")
        for session_id, record in data["records"].items():
            _validate_record(record, self._guild_id)
            if session_id != record["session_id"]:
                raise AdmissionConflict("参加承認の識別子が一致しません。")
        return data["records"]

    async def get(self, session_id):
        if not _session_id(session_id):
            raise AdmissionConflict("Welcomeの識別子が不正です。")
        record = self._records(await self._store.read()).get(session_id)
        return (
            None if record is None
            else {**record, "role_ids": list(record["role_ids"])}
        )

    async def begin(self, *, context, plan, actor_id, role_ids, now=None):
        """権限・回答の確認を済ませた呼び出し元から承認を記録する。"""
        if (
            plan.status != "confirmed"
            or plan.guild_id != self._guild_id
            or context.guild_id != self._guild_id
            or plan.welcome_session_id != context.session_id
            or plan.user_id != context.user_id
            or plan.channel_id != context.channel_id
        ):
            raise AdmissionConflict("確定した面談とWelcomeが一致しません。")

        timestamp = now or datetime.now(timezone.utc)
        if timestamp.utcoffset() is None:
            raise AdmissionConflict("タイムゾーン付きの日時が必要です。")

        candidate = {
            "session_id": context.session_id,
            "proposal_id": plan.proposal_id,
            "guild_id": self._guild_id,
            "user_id": context.user_id,
            "channel_id": context.channel_id,
            "approved_by": actor_id,
            "role_ids": sorted(role_ids),
            "approved_at": timestamp.astimezone(timezone.utc).isoformat(),
            "phase": "approved",
        }
        _validate_record(candidate, self._guild_id)
        result = None

        def change(data):
            nonlocal result
            records = self._records(data)
            previous = records.get(context.session_id)
            if previous is not None:
                # 再クリックでは最初の承認者・日時を保持する。
                for key in (
                    "proposal_id", "guild_id", "user_id",
                    "channel_id", "role_ids",
                ):
                    if previous[key] != candidate[key]:
                        raise AdmissionConflict(
                            "保存済みの承認内容と一致しません。"
                        )
                result = previous
                return data

            records[context.session_id] = candidate
            result = candidate
            return {"version": 1, "records": records}

        await self._store.update(change)
        return {**result, "role_ids": list(result["role_ids"])}

    async def _advance(self, session_id, proposal_id, target):
        if not _session_id(session_id) or not _session_id(proposal_id):
            raise AdmissionConflict("承認の識別子が不正です。")
        result = None

        def change(data):
            nonlocal result
            records = self._records(data)
            current = records.get(session_id)
            if current is None or current["proposal_id"] != proposal_id:
                raise AdmissionConflict("対象の参加承認が見つかりません。")

            current_index = PHASES.index(current["phase"])
            target_index = PHASES.index(target)
            if current_index >= target_index:
                result = current
                return data
            if target_index != current_index + 1:
                raise AdmissionConflict(
                    "ロール付与の確認前に完了にはできません。"
                )

            result = {**current, "phase": target}
            records[session_id] = result
            return {"version": 1, "records": records}

        await self._store.update(change)
        return {**result, "role_ids": list(result["role_ids"])}

    async def mark_roles_verified(self, session_id, proposal_id):
        return await self._advance(session_id, proposal_id, "roles_verified")

    async def mark_completed(self, session_id, proposal_id):
        return await self._advance(session_id, proposal_id, "completed")

    async def pending(self):
        records = self._records(await self._store.read())
        return tuple(
            {**record, "role_ids": list(record["role_ids"])}
            for record in records.values()
            if record["phase"] != "completed"
        )

    async def close(self):
        await self._store.close()
