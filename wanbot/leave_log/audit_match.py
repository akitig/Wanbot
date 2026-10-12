"""取得済みの監査ログ候補を退出情報と照合する。"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from wanbot.leave_log.models import DepartureDataError


MATCH_WINDOW = timedelta(seconds=30)


def _id(value):
    if type(value) is not int or not 0 < value < 2**64:
        raise DepartureDataError("監査ログのIDが不正です。")


@dataclass(frozen=True)
class AuditEvidence:
    entry_id: int
    guild_id: int
    target_id: int
    action: str
    created_at: datetime
    actor_id: int | None = None
    reason: str | None = None
    actor_username: str | None = None
    actor_display_name: str | None = None

    def __post_init__(self):
        for value in (self.entry_id, self.guild_id, self.target_id):
            _id(value)
        if self.actor_id is not None:
            _id(self.actor_id)
        if self.action not in ("kick", "ban"):
            raise DepartureDataError("監査ログの操作種類が不正です。")
        if (
            not isinstance(self.created_at, datetime)
            or self.created_at.utcoffset() is None
        ):
            raise DepartureDataError("監査ログの日時が不正です。")
        if self.reason is not None and type(self.reason) is not str:
            raise DepartureDataError("監査ログの理由が不正です。")
        for value in (self.actor_username, self.actor_display_name):
            if value is not None and (
                type(value) is not str or not value.strip()
            ):
                raise DepartureDataError("実行者の名前が不正です。")
        if self.actor_id is None and (
            self.actor_username is not None
            or self.actor_display_name is not None
        ):
            raise DepartureDataError("実行者のIDがありません。")


@dataclass(frozen=True)
class AuditMatch:
    kind: str
    evidence: AuditEvidence | None
    explanation: str


def match_audit(
    snapshot, entries, *, lookup_complete, used_entry_ids=()
):
    """lookup_completeは必要な範囲の取得を終えた場合だけTrueにする。"""
    if type(lookup_complete) is not bool:
        raise DepartureDataError("監査ログの取得状態が不正です。")
    if not lookup_complete:
        return AuditMatch("unknown", None, "audit_unavailable_or_incomplete")

    used = set(used_entry_ids)
    candidates = {}
    lower = snapshot.observed_at - MATCH_WINDOW
    upper = snapshot.observed_at + MATCH_WINDOW

    for entry in entries:
        if not isinstance(entry, AuditEvidence):
            raise DepartureDataError("監査ログ候補の形式が不正です。")
        if (
            entry.guild_id != snapshot.guild_id
            or entry.target_id != snapshot.user_id
            or entry.entry_id in used
            or not lower <= entry.created_at <= upper
        ):
            continue
        if (
            snapshot.joined_at is not None
            and entry.created_at < snapshot.joined_at
        ):
            continue

        previous = candidates.get(entry.entry_id)
        if previous is not None and previous != entry:
            return AuditMatch("unknown", None, "conflicting_audit_data")
        candidates[entry.entry_id] = entry

    if not candidates:
        return AuditMatch("unknown", None, "no_matching_audit")
    if len(candidates) != 1:
        return AuditMatch("unknown", None, "multiple_matching_audits")

    evidence = next(iter(candidates.values()))
    return AuditMatch(evidence.action, evidence, "matched")
