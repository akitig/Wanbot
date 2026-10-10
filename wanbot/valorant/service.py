"""診断の開始・回答・結果保存・復旧をまとめる。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from wanbot.config import AppConfig
from wanbot.valorant.repository import DiagnosisRepository
from wanbot.valorant.scoring import (
    ClassificationPolicy,
    DiagnosisResult,
    DiagnosisScorer,
)
from wanbot.valorant.session import DiagnosisSession, SessionStatus
from wanbot.valorant.session_repository import SessionRepository


@dataclass(frozen=True)
class DiagnosisProgress:
    session: DiagnosisSession
    result: DiagnosisResult | None = None


class DiagnosisService:
    """サーバーごとに1つ作り、同じイベントループで使用する。"""

    def __init__(
        self,
        config: AppConfig,
        guild_id: int,
        scorer: DiagnosisScorer,
    ) -> None:
        self._scorer = scorer
        self._sessions = SessionRepository(
            config,
            guild_id,
            scorer.question_set,
        )
        self._results = DiagnosisRepository(config, guild_id, scorer)

        # 同じサーバーの診断操作と終了処理が途中で交差するのを防ぐ。
        self._lock = asyncio.Lock()
        self._closed = False

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("終了済みの診断サービスは使用できません。")

    async def start(
        self,
        *,
        user_id: int,
        invoker_id: int,
        timeout_seconds: int,
        now: datetime,
    ) -> DiagnosisProgress:
        async with self._lock:
            self._ensure_open()
            session = await self._sessions.start(
                user_id=user_id,
                invoker_id=invoker_id,
                timeout_seconds=timeout_seconds,
                now=now,
            )
            return DiagnosisProgress(session=session)

    async def answer(
        self,
        *,
        user_id: int,
        session_id: str,
        actor_id: int,
        question_id: str,
        choice_id: str,
        now: datetime,
    ) -> DiagnosisProgress:
        async with self._lock:
            self._ensure_open()
            session = await self._sessions.answer(
                user_id=user_id,
                session_id=session_id,
                actor_id=actor_id,
                question_id=question_id,
                choice_id=choice_id,
                now=now,
            )
            return await self._finish_if_complete(session, now)

    async def resume(
        self,
        user_id: int,
        *,
        now: datetime,
    ) -> DiagnosisProgress | None:
        """途中の診断を復元し、回答完了済みなら結果保存を再実行する。"""
        async with self._lock:
            self._ensure_open()
            session = await self._sessions.get(user_id, now=now)
            if session is None:
                return None
            return await self._finish_if_complete(session, now)

    async def _finish_if_complete(
        self,
        session: DiagnosisSession,
        now: datetime,
    ) -> DiagnosisProgress:
        if session.status is not SessionStatus.COMPLETED:
            return DiagnosisProgress(session=session)

        if session.completed_at is None:
            raise RuntimeError("完了済みの診断に完了日時がありません。")

        result = self._scorer.evaluate(session.answers)

        # 復旧時にも、最初に回答を完了した日時を使う。
        await self._results.save_completed(
            user_id=session.user_id,
            invoker_id=session.invoker_id,
            answers=session.answers,
            completed_at=session.completed_at,
        )

        # 結果の保存に成功してから、進行データを削除する。
        await self._sessions.acknowledge_completed(
            user_id=session.user_id,
            session_id=session.session_id,
            now=now,
        )
        return DiagnosisProgress(session=session, result=result)


    async def cancel_unanswered(
        self,
        *,
        user_id: int,
        session_id: str,
        now: datetime,
    ) -> None:
        """未回答の診断だけを取り消す。"""
        async with self._lock:
            self._ensure_open()
            await self._sessions.cancel_unanswered(
                user_id=user_id,
                session_id=session_id,
                now=now,
            )

    async def list_sessions(
        self,
        *,
        now: datetime,
    ) -> list[DiagnosisSession]:
        async with self._lock:
            self._ensure_open()
            return await self._sessions.list_sessions(now=now)

    async def latest_result(self, user_id: int) -> DiagnosisResult | None:
        """保存時の判定基準を使い、最新の結果を表示用に復元する。"""
        async with self._lock:
            self._ensure_open()
            record = await self._results.latest(user_id)
            if record is None:
                return None

            saved_scorer = DiagnosisScorer(
                self._scorer.question_set,
                policy=ClassificationPolicy(**record["policy"]),
            )
            return saved_scorer.evaluate(record["answers"])

    async def latest_record(self, user_id: int) -> dict | None:
        """所属サーバーの、検証済みの最新診断を読み出す。"""
        async with self._lock:
            self._ensure_open()
            return await self._results.latest(user_id)

    async def close(self) -> None:
        """実行中の操作を待ってから保存先を閉じる。"""
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                await self._sessions.close()
            finally:
                await self._results.close()
