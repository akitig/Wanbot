"""Welcomeの案内ごとにVALORANT診断を保存する。"""

import re

from wanbot.valorant.service import DiagnosisService
from wanbot.valorant.session import SessionError, SessionStatus
from wanbot.valorant.session_repository import SessionRepository


class WelcomeDiagnosisSessions(SessionRepository):
    """診断完了後も進行データを残し、同じ結果を再表示できるようにする。"""

    def __init__(self, config, guild_id, question_set, *, welcome_session_id):
        if (
            not isinstance(welcome_session_id, str)
            or re.fullmatch(r"[0-9a-f]{32}", welcome_session_id) is None
        ):
            raise ValueError("Invalid Welcome session ID")

        super().__init__(
            config,
            guild_id,
            question_set,
            document_name=f"welcome_valorant_{welcome_session_id}",
        )

    async def acknowledge_completed(
        self,
        *,
        user_id,
        session_id,
        now,
    ):
        # 共通サービスは、結果保存後にこのメソッドを呼ぶ。
        # Welcomeでは削除せず、完了した診断を保持する。
        session = await self.get(user_id, now=now)
        if (
            session is None
            or session.session_id != session_id
            or session.status is not SessionStatus.COMPLETED
        ):
            raise SessionError("Completed Welcome diagnosis does not match")


class WelcomeDiagnosisService(DiagnosisService):
    def __init__(
        self,
        config,
        guild_id,
        scorer,
        *,
        welcome_session_id,
    ):
        sessions = WelcomeDiagnosisSessions(
            config,
            guild_id,
            scorer.question_set,
            welcome_session_id=welcome_session_id,
        )
        super().__init__(
            config,
            guild_id,
            scorer,
            session_repository=sessions,
        )
