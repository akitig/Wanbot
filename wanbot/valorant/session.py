"""1人分の診断の進行を管理する。Discordへの通信は行わない。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from enum import Enum
from types import MappingProxyType
from uuid import uuid4

from wanbot.valorant.questions import Question, QuestionSet


class SessionError(ValueError):
    """回答を受け付けられない状態や操作を表す。"""


class SessionStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    EXPIRED = "expired"


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("日時にはタイムゾーンを指定してください。")
    return value.astimezone(timezone.utc)


class DiagnosisSession:
    def __init__(
        self,
        *,
        guild_id: int,
        user_id: int,
        invoker_id: int,
        question_set: QuestionSet,
        started_at: datetime,
        timeout_seconds: int,
    ) -> None:
        for value in (guild_id, user_id, invoker_id):
            if type(value) is not int or not 0 < value < 2**64:
                raise ValueError("サーバー・ユーザーIDが不正です。")

        if type(timeout_seconds) is not int or timeout_seconds <= 0:
            raise ValueError("制限時間は正の整数の秒数で指定してください。")

        if not question_set.questions:
            raise ValueError("診断には質問が必要です。")

        self.session_id = uuid4().hex
        self.guild_id = guild_id
        self.user_id = user_id
        self.invoker_id = invoker_id
        self.question_set = question_set
        self.started_at = _utc(started_at)
        self.expires_at = self.started_at + timedelta(seconds=timeout_seconds)

        self._status = SessionStatus.ACTIVE
        self._answers: dict[str, str] = {}
        self._completed_at: datetime | None = None

    @property
    def status(self) -> SessionStatus:
        return self._status

    @property
    def completed_at(self) -> datetime | None:
        return self._completed_at

    @property
    def answers(self) -> Mapping[str, str]:
        # 呼び出し側から回答を書き換えられないコピーを返す。
        return MappingProxyType(dict(self._answers))

    @property
    def current_question(self) -> Question | None:
        if self._status is not SessionStatus.ACTIVE:
            return None
        return self.question_set.questions[len(self._answers)]

    def expire_if_due(self, now: datetime) -> bool:
        """期限に達した進行中の診断だけを、時間切れにする。"""
        current_time = _utc(now)
        if (
            self._status is SessionStatus.ACTIVE
            and current_time >= self.expires_at
        ):
            self._status = SessionStatus.EXPIRED
            return True
        return False

    def submit(
        self,
        *,
        actor_id: int,
        question_id: str,
        choice_id: str,
        now: datetime,
    ) -> SessionStatus:
        """現在の質問への回答を1回だけ受け付ける。"""
        if type(actor_id) is not int or actor_id != self.user_id:
            raise SessionError("この診断は本人だけが回答できます。")

        current_time = _utc(now)
        if current_time < self.started_at:
            raise SessionError("診断開始前の回答は受け付けられません。")

        self.expire_if_due(current_time)

        if self._status is SessionStatus.EXPIRED:
            raise SessionError("回答期限を過ぎています。")
        if self._status is SessionStatus.COMPLETED:
            raise SessionError("この診断は回答済みです。")

        question = self.current_question
        if question is None or question.id != question_id:
            raise SessionError("現在の質問に回答してください。")

        if type(choice_id) is not str or not any(
            choice.id == choice_id for choice in question.choices
        ):
            raise SessionError("選択肢が不正です。")

        self._answers[question.id] = choice_id

        if len(self._answers) == len(self.question_set.questions):
            self._status = SessionStatus.COMPLETED
            self._completed_at = current_time

        return self._status
