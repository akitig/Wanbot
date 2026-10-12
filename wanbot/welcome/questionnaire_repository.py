"""Welcomeの質問への回答を、案内ごとに保存する。"""

from dataclasses import dataclass
import re

from wanbot.storage.async_store import AsyncGuildJsonStore
from wanbot.welcome.questionnaire import (
    Questionnaire,
    answer,
    edit_text_answer,
)


class QuestionnaireDataError(ValueError):
    """保存データが不正。回答本文はエラーへ含めない。"""


class QuestionnaireConflict(ValueError):
    """回答者・進行状態・更新番号が一致しない。"""


@dataclass(frozen=True)
class QuestionnaireSnapshot:
    session_id: str
    user_id: int
    revision: int
    progress: Questionnaire


def _session_id(value):
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[0-9a-f]{32}", value) is None
    ):
        raise ValueError("Invalid Welcome session ID")


def _user_id(value):
    if type(value) is not int or not 0 < value < 1 << 64:
        raise ValueError("Invalid Discord user ID")


def _apply(progress, event):
    if (
        not isinstance(event, dict)
        or set(event) != {"kind", "question", "value"}
        or not isinstance(event["question"], str)
        or not isinstance(event["value"], str)
    ):
        raise ValueError("Invalid answer event")

    if event["kind"] == "answer":
        return answer(progress, event["question"], event["value"])
    if event["kind"] == "edit":
        return edit_text_answer(
            progress, event["question"], event["value"]
        )
    raise ValueError("Invalid answer event kind")


def _decode(data):
    if data == {}:
        return {}

    try:
        if (
            not isinstance(data, dict)
            or set(data) != {"state_version", "records"}
            or type(data["state_version"]) is not int
            or data["state_version"] != 1
            or not isinstance(data["records"], dict)
        ):
            raise ValueError

        result = {}
        for session_id, record in data["records"].items():
            _session_id(session_id)
            if (
                not isinstance(record, dict)
                or set(record) != {"user_id", "revision", "events"}
            ):
                raise ValueError
            _user_id(record["user_id"])

            events = record["events"]
            revision = record["revision"]
            if (
                not isinstance(events, list)
                or type(revision) is not int
                or revision != len(events)
            ):
                raise ValueError

            progress = Questionnaire()
            for event in events:
                progress = _apply(progress, event)

            result[session_id] = QuestionnaireSnapshot(
                session_id=session_id,
                user_id=record["user_id"],
                revision=revision,
                progress=progress,
            )
        return result
    except (ValueError, TypeError, KeyError, AttributeError):
        raise QuestionnaireDataError(
            "Invalid Welcome questionnaire data"
        ) from None


class QuestionnaireRepository:
    def __init__(self, config, guild_id):
        self._store = AsyncGuildJsonStore(
            config, guild_id, "welcome_questionnaires"
        )

    async def ensure(self, session):
        """有効なWelcome案内に、回答用の記録を用意する。"""
        _session_id(session.session_id)
        _user_id(session.user_id)
        if session.state != "active":
            raise QuestionnaireConflict("Welcome is not active")

        result = None

        def change(data):
            nonlocal result
            snapshots = _decode(data)
            existing = snapshots.get(session.session_id)

            if existing is not None:
                if existing.user_id != session.user_id:
                    raise QuestionnaireConflict(
                        "Questionnaire owner does not match"
                    )
                result = existing
                return data

            if not data:
                data = {"state_version": 1, "records": {}}

            data["records"][session.session_id] = {
                "user_id": session.user_id,
                "revision": 0,
                "events": [],
            }
            result = QuestionnaireSnapshot(
                session_id=session.session_id,
                user_id=session.user_id,
                revision=0,
                progress=Questionnaire(),
            )
            return data

        await self._store.update(change)
        assert result is not None
        return result

    async def get(self, session_id):
        _session_id(session_id)
        return _decode(await self._store.read()).get(session_id)

    async def submit(
        self,
        session_id,
        *,
        actor_id,
        expected_revision,
        question,
        value,
    ):
        return await self._save(
            session_id,
            actor_id=actor_id,
            expected_revision=expected_revision,
            kind="answer",
            question=question,
            value=value,
        )

    async def edit(
        self,
        session_id,
        *,
        actor_id,
        expected_revision,
        question,
        value,
    ):
        return await self._save(
            session_id,
            actor_id=actor_id,
            expected_revision=expected_revision,
            kind="edit",
            question=question,
            value=value,
        )

    async def _save(
        self,
        session_id,
        *,
        actor_id,
        expected_revision,
        kind,
        question,
        value,
    ):
        _session_id(session_id)
        _user_id(actor_id)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("Invalid questionnaire revision")

        result = None

        def change(data):
            nonlocal result
            snapshots = _decode(data)
            current = snapshots.get(session_id)

            if current is None:
                raise QuestionnaireConflict("Questionnaire was not found")
            if current.user_id != actor_id:
                raise QuestionnaireConflict("Only the owner can answer")
            if current.revision != expected_revision:
                raise QuestionnaireConflict("Questionnaire has changed")

            event = {
                "kind": kind,
                "question": question,
                "value": value,
            }
            progress = _apply(current.progress, event)

            record = data["records"][session_id]
            record["events"].append(event)
            record["revision"] += 1

            result = QuestionnaireSnapshot(
                session_id=session_id,
                user_id=current.user_id,
                revision=record["revision"],
                progress=progress,
            )
            return data

        await self._store.update(change)
        assert result is not None
        return result

    async def submit_batch(
        self, session_id, *, actor_id, expected_revision, answers
    ):
        """フォームの回答を、全件検証してから一括保存する。"""
        _session_id(session_id)
        _user_id(actor_id)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("Invalid questionnaire revision")

        answers = tuple(answers)
        if not answers or len(answers) > 5:
            raise ValueError("Invalid batch size")

        result = None

        def change(data):
            nonlocal result
            current = _decode(data).get(session_id)
            if current is None:
                raise QuestionnaireConflict("Questionnaire was not found")
            if current.user_id != actor_id:
                raise QuestionnaireConflict("Only the owner can answer")
            if current.revision != expected_revision:
                raise QuestionnaireConflict("Questionnaire has changed")

            progress = current.progress
            events = []
            for question, value in answers:
                event = {
                    "kind": "answer",
                    "question": question,
                    "value": value,
                }
                progress = _apply(progress, event)
                events.append(event)

            # 全回答が正しいことを確認できてから変更する。
            record = data["records"][session_id]
            record["events"].extend(events)
            record["revision"] += len(events)
            result = QuestionnaireSnapshot(
                session_id=session_id,
                user_id=current.user_id,
                revision=record["revision"],
                progress=progress,
            )
            return data

        await self._store.update(change)
        assert result is not None
        return result

    async def close(self):
        await self._store.close()
