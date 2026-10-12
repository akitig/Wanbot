"""退出の照合結果と、使用済み監査ログをサーバーごとに保存する。"""

from copy import deepcopy
from datetime import datetime, timezone

from wanbot.leave_log.audit_match import AuditEvidence, AuditMatch, match_audit
from wanbot.leave_log.models import decode_snapshot, encode_snapshot
from wanbot.storage.async_store import AsyncGuildJsonStore


class DepartureResultConflict(ValueError):
    pass


UNKNOWN_REASONS = {
    "audit_unavailable_or_incomplete",
    "conflicting_audit_data",
    "no_matching_audit",
    "multiple_matching_audits",
}


def encode_result(snapshot, result):
    evidence = result.evidence
    if result.kind == "unknown":
        if evidence is not None or result.explanation not in UNKNOWN_REASONS:
            raise DepartureResultConflict("判別不能の結果が不正です。")
        encoded = None
    elif result.kind in ("kick", "ban"):
        if (
            not isinstance(evidence, AuditEvidence)
            or result.explanation != "matched"
            or evidence.action != result.kind
            or match_audit(
                snapshot, [evidence], lookup_complete=True
            ).kind != result.kind
        ):
            raise DepartureResultConflict("監査ログと退出情報が一致しません。")
        encoded = {
            "entry_id": evidence.entry_id,
            "guild_id": evidence.guild_id,
            "target_id": evidence.target_id,
            "action": evidence.action,
            "created_at": evidence.created_at.astimezone(timezone.utc).isoformat(),
            "actor_id": evidence.actor_id,
            "reason": evidence.reason,
        }
        # 名前がない既存の記録は、従来と同じ形式で扱う。
        if evidence.actor_username is not None:
            encoded["actor_username"] = evidence.actor_username
        if evidence.actor_display_name is not None:
            encoded["actor_display_name"] = evidence.actor_display_name
    else:
        raise DepartureResultConflict("退出の種類が不正です。")

    return {
        "snapshot": encode_snapshot(snapshot),
        "kind": result.kind,
        "explanation": result.explanation,
        "evidence": encoded,
    }


def decode_result(record):
    if type(record) is not dict or set(record) != {
        "snapshot", "kind", "explanation", "evidence"
    }:
        raise DepartureResultConflict("照合結果の保存形式が不正です。")

    snapshot = decode_snapshot(record["snapshot"])
    raw = record["evidence"]
    evidence = None
    if raw is not None:
        required = {
            "entry_id", "guild_id", "target_id", "action",
            "created_at", "actor_id", "reason",
        }
        optional = {"actor_username", "actor_display_name"}
        if (
            type(raw) is not dict
            or not required <= set(raw)
            or not set(raw) <= required | optional
        ):
            raise DepartureResultConflict("監査ログの保存形式が不正です。")
        try:
            if type(raw["created_at"]) is not str:
                raise ValueError
            evidence = AuditEvidence(
                **{
                    **raw,
                    "created_at": datetime.fromisoformat(raw["created_at"]),
                }
            )
        except (TypeError, ValueError, OverflowError):
            raise DepartureResultConflict("監査ログの保存内容が不正です。") from None

    result = AuditMatch(
        record["kind"], evidence, record["explanation"]
    )
    encode_result(snapshot, result)
    return snapshot, result


class DepartureResultRepository:
    def __init__(self, config, guild_id, *, store=None):
        if type(guild_id) is not int or not 0 < guild_id < 2**64:
            raise DepartureResultConflict("サーバーIDが不正です。")
        self._guild_id = guild_id
        self._store = (
            store if store is not None else
            AsyncGuildJsonStore(config, guild_id, "leave_log_results")
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
            raise DepartureResultConflict("照合結果の保存形式が不正です。")

        used = set()
        for event_id, record in data["records"].items():
            snapshot, result = decode_result(record)
            if (
                snapshot.event_id != event_id
                or snapshot.guild_id != self._guild_id
            ):
                raise DepartureResultConflict("照合結果の所属が一致しません。")
            if result.evidence is not None:
                entry_id = result.evidence.entry_id
                if entry_id in used:
                    raise DepartureResultConflict("監査ログが重複使用されています。")
                used.add(entry_id)
        return data["records"]

    async def get(self, event_id):
        record = self._records(await self._store.read()).get(event_id)
        return None if record is None else decode_result(record)

    async def used_entry_ids(self):
        records = self._records(await self._store.read())
        return frozenset(
            record["evidence"]["entry_id"]
            for record in records.values()
            if record["evidence"] is not None
        )

    async def save(self, snapshot, result):
        candidate = encode_result(snapshot, result)
        self._records({
            "version": 1,
            "records": {snapshot.event_id: candidate},
        })

        def change(data):
            records = dict(self._records(data))
            previous = records.get(snapshot.event_id)
            if previous is not None:
                if previous != candidate:
                    raise DepartureResultConflict(
                        "この退出には別の確定結果が保存されています。"
                    )
                return data

            records[snapshot.event_id] = deepcopy(candidate)
            updated = {"version": 1, "records": records}
            # 保存直前にも、他の退出で監査ログを使用していないか確認。
            self._records(updated)
            return updated

        await self._store.update(change)

    async def close(self):
        await self._store.close()
