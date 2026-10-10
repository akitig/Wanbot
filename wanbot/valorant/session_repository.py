"""診断の進行状況をサーバー別に保存する。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from wanbot.config import AppConfig
from wanbot.storage.async_store import AsyncGuildJsonStore
from wanbot.storage.json_store import StorageError
from wanbot.valorant.questions import QuestionSet
from wanbot.valorant.session import (
    DiagnosisSession,
    SessionError,
    SessionStatus,
)
from wanbot.valorant.session_data import session_from_data, session_to_data


def _check_user_id(user_id: int) -> None:
    if type(user_id) is not int or not 0 < user_id < 2**64:
        raise ValueError("ユーザーIDが不正です。")


class SessionRepository:
    def __init__(
        self,
        config: AppConfig,
        guild_id: int,
        question_set: QuestionSet,
    ) -> None:
        self._guild_id = guild_id
        self._question_set = question_set
        self._store = AsyncGuildJsonStore(
            config,
            guild_id,
            "valorant_sessions",
        )

    def _restore(
        self,
        record: dict[str, Any],
        now: datetime,
    ) -> DiagnosisSession:
        return session_from_data(
            record,
            question_set=self._question_set,
            expected_guild_id=self._guild_id,
            now=now,
        )

    def _entries(
        self,
        data: dict[str, Any],
        now: datetime,
    ) -> dict[str, Any]:
        if not data:
            return {}

        if set(data) != {"sessions"} or type(data["sessions"]) is not dict:
            raise StorageError("診断進行データの形式が不正です。")

        entries = data["sessions"]
        for key, record in entries.items():
            session = self._restore(record, now)
            if key != str(session.user_id):
                raise StorageError("診断進行データのユーザーIDが一致しません。")

        return entries

    async def get(
        self,
        user_id: int,
        *,
        now: datetime,
    ) -> DiagnosisSession | None:
        _check_user_id(user_id)
        data = await self._store.read()
        record = self._entries(data, now).get(str(user_id))
        if record is None:
            return None
        return self._restore(record, now)

    async def start(
        self,
        *,
        user_id: int,
        invoker_id: int,
        timeout_seconds: int,
        now: datetime,
    ) -> DiagnosisSession:
        candidate = DiagnosisSession(
            guild_id=self._guild_id,
            user_id=user_id,
            invoker_id=invoker_id,
            question_set=self._question_set,
            started_at=now,
            timeout_seconds=timeout_seconds,
        )

        def change(data: dict[str, Any]) -> dict[str, Any]:
            entries = self._entries(data, now)
            previous = entries.get(str(user_id))

            if previous is not None:
                existing = self._restore(previous, now)
                if existing.status is SessionStatus.ACTIVE:
                    raise SessionError("この人の診断はすでに進行中です。")
                if existing.status is SessionStatus.COMPLETED:
                    raise SessionError("前回の診断結果を保存処理中です。")

            # 時間切れの診断は、新しい診断で置き換えられる。
            entries[str(user_id)] = session_to_data(candidate)
            return {"sessions": entries}

        await self._store.update(change)
        return candidate

    async def answer(
        self,
        *,
        user_id: int,
        session_id: str,
        actor_id: int,
        question_id: str,
        choice_id: str,
        now: datetime,
    ) -> DiagnosisSession:
        _check_user_id(user_id)
        updated: list[DiagnosisSession] = []

        def change(data: dict[str, Any]) -> dict[str, Any]:
            entries = self._entries(data, now)
            record = entries.get(str(user_id))
            if record is None:
                raise SessionError("この診断は終了しているか、存在しません。")

            session = self._restore(record, now)
            if session.session_id != session_id:
                raise SessionError("古い診断のボタンは使用できません。")

            session.submit(
                actor_id=actor_id,
                question_id=question_id,
                choice_id=choice_id,
                now=now,
            )
            entries[str(user_id)] = session_to_data(session)
            updated.append(session)
            return {"sessions": entries}

        # 読み込み・回答確認・書き込みを一つの更新処理として実行する。
        # 同時にボタンが押されても、同じ質問を二重に回答できない。
        await self._store.update(change)
        return updated[0]

    async def acknowledge_completed(
        self,
        *,
        user_id: int,
        session_id: str,
        now: datetime,
    ) -> None:
        """診断結果の保存に成功した後で、完了済みの進行データを削除する。"""
        _check_user_id(user_id)

        def change(data: dict[str, Any]) -> dict[str, Any]:
            entries = self._entries(data, now)
            record = entries.get(str(user_id))
            if record is None:
                return {"sessions": entries}

            session = self._restore(record, now)
            if session.session_id != session_id:
                raise SessionError("別の診断の進行データは削除できません。")
            if session.status is not SessionStatus.COMPLETED:
                raise SessionError("回答が完了していない診断は削除できません。")

            del entries[str(user_id)]
            return {"sessions": entries}

        await self._store.update(change)


    async def cancel_unanswered(
        self,
        *,
        user_id: int,
        session_id: str,
        now: datetime,
    ) -> None:
        """送信拒否などで開始できなかった、未回答の診断だけを取り消す。"""
        _check_user_id(user_id)

        def change(data: dict[str, Any]) -> dict[str, Any]:
            entries = self._entries(data, now)
            record = entries.get(str(user_id))
            if record is None:
                return {"sessions": entries}

            session = self._restore(record, now)
            if session.session_id != session_id:
                raise SessionError("別の診断は取り消せません。")
            if session.answers or session.status is SessionStatus.COMPLETED:
                raise SessionError("回答済みの診断は取り消せません。")

            del entries[str(user_id)]
            return {"sessions": entries}

        await self._store.update(change)

    async def list_sessions(
        self,
        *,
        now: datetime,
    ) -> list[DiagnosisSession]:
        """保存された診断を検証して読み出す。期限は延長しない。"""
        data = await self._store.read()
        entries = self._entries(data, now)
        return [
            self._restore(record, now)
            for record in entries.values()
        ]

    async def close(self) -> None:
        await self._store.close()
