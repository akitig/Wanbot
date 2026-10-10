"""診断の進行状況を保存用データへ変換し、検証して復元する。"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from wanbot.storage.json_store import StorageError
from wanbot.valorant.questions import QuestionSet
from wanbot.valorant.session import DiagnosisSession, SessionStatus


SESSION_DATA_VERSION = 1
SESSION_ID_PATTERN = re.compile(r"[0-9a-f]{32}")


def session_to_data(session: DiagnosisSession) -> dict[str, Any]:
    """JSONとして保存できる、新しい辞書を返す。"""
    return {
        "schema_version": SESSION_DATA_VERSION,
        "session_id": session.session_id,
        "guild_id": session.guild_id,
        "user_id": session.user_id,
        "invoker_id": session.invoker_id,
        "diagnosis_version": session.question_set.diagnosis_version,
        "started_at": session.started_at.isoformat(),
        "timeout_seconds": int(
            (session.expires_at - session.started_at).total_seconds()
        ),
        "status": session.status.value,
        "completed_at": (
            session.completed_at.isoformat()
            if session.completed_at is not None
            else None
        ),
        "answers": dict(session.answers),
    }


def session_from_data(
    data: dict[str, Any],
    *,
    question_set: QuestionSet,
    expected_guild_id: int,
    now: datetime,
) -> DiagnosisSession:
    """保存内容を検証して復元する。期限を過ぎていれば時間切れにする。"""
    try:
        return _restore(
            data,
            question_set=question_set,
            expected_guild_id=expected_guild_id,
            now=now,
        )
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError):
        # 保存された回答やIDを、エラー本文に含めない。
        raise StorageError(
            "診断の進行状況を復元できません。"
            "保存形式・所属サーバー・質問バージョンを確認してください。"
        ) from None


def _restore(
    data: dict[str, Any],
    *,
    question_set: QuestionSet,
    expected_guild_id: int,
    now: datetime,
) -> DiagnosisSession:
    if type(data) is not dict:
        raise ValueError

    if (
        type(data["schema_version"]) is not int
        or data["schema_version"] != SESSION_DATA_VERSION
    ):
        raise ValueError

    if (
        type(expected_guild_id) is not int
        or type(data["guild_id"]) is not int
        or data["guild_id"] != expected_guild_id
    ):
        raise ValueError

    if data["diagnosis_version"] != question_set.diagnosis_version:
        raise ValueError

    session_id = data["session_id"]
    if (
        type(session_id) is not str
        or SESSION_ID_PATTERN.fullmatch(session_id) is None
    ):
        raise ValueError

    status = SessionStatus(data["status"])
    answers = data["answers"]
    if type(answers) is not dict:
        raise ValueError

    # 回答済みの質問は、必ず先頭から連続している必要がある。
    answered_questions = question_set.questions[:len(answers)]
    if set(answers) != {question.id for question in answered_questions}:
        raise ValueError

    session = DiagnosisSession(
        guild_id=data["guild_id"],
        user_id=data["user_id"],
        invoker_id=data["invoker_id"],
        question_set=question_set,
        started_at=datetime.fromisoformat(data["started_at"]),
        timeout_seconds=data["timeout_seconds"],
    )
    session.session_id = session_id

    # 保存内容を既存の回答処理へ通し、不正な選択肢や状態を検出する。
    # 各問の回答時刻は保存していないため、進行中は開始日時で再現する。
    replay_time = session.started_at
    if status is SessionStatus.COMPLETED:
        replay_time = datetime.fromisoformat(data["completed_at"])

    for question in answered_questions:
        session.submit(
            actor_id=session.user_id,
            question_id=question.id,
            choice_id=answers[question.id],
            now=replay_time,
        )

    if status is SessionStatus.EXPIRED:
        session.expire_if_due(session.expires_at)

    # 状態と回答数の矛盾、余分な項目、型の違いも拒否する。
    actual = json.dumps(
        session_to_data(session),
        sort_keys=True,
        allow_nan=False,
    )
    expected = json.dumps(data, sort_keys=True, allow_nan=False)
    if actual != expected:
        raise ValueError

    # 元の期限を維持する。完了済みの診断は時間切れに変更しない。
    session.expire_if_due(now)
    return session
