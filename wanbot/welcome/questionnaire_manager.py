"""案内中のWelcomeに質問パネルを用意し、再起動後に復旧する。"""

import logging

from wanbot.logging_config import log_event
from wanbot.welcome.questionnaire_controller import QuestionnaireController
from wanbot.welcome.questionnaire_forms import QuestionnaireContext
from wanbot.welcome.questionnaire_panel import QuestionnairePanel


class WelcomeQuestionnaires:
    def __init__(
        self,
        *,
        client,
        guild_id,
        welcome_repository,
        questionnaire_repository,
        panel_repository,
        lock,
        is_available,
    ):
        self._client = client
        self._guild_id = guild_id
        self._welcome = welcome_repository
        self._questions = questionnaire_repository
        self._panels = panel_repository
        self._lock = lock
        self._is_available = is_available
        self._entries = {}
        self._retry = set()
        self._closed = False

    def _available(self):
        return not self._closed and self._is_available()

    def _discard(self, session_id):
        entry = self._entries.pop(session_id, None)
        if entry is not None:
            controller, panel = entry
            controller.close()
            panel.close()
        self._retry.discard(session_id)

    async def ensure(self, session):
        async with self._lock:
            if self._closed:
                return None

            current = await self._welcome.get_active(session.user_id)
            if (
                current is None
                or current.session_id != session.session_id
                or current.state != "active"
                or current.channel_id != session.channel_id
            ):
                self._discard(session.session_id)
                return None

            if session.session_id not in self._entries:
                context = QuestionnaireContext(
                    guild_id=self._guild_id,
                    channel_id=current.channel_id,
                    user_id=current.user_id,
                    session_id=current.session_id,
                    is_available=self._available,
                )
                controller = QuestionnaireController(
                    context=context,
                    welcome_repository=self._welcome,
                    questionnaire_repository=self._questions,
                    lock=self._lock,
                )
                panel = QuestionnairePanel(
                    client=self._client,
                    context=context,
                    repository=self._panels,
                    view=controller.start_view,
                )
                self._entries[session.session_id] = (controller, panel)

            controller, panel = self._entries[session.session_id]
            try:
                message_id = await panel.ensure()
            except Exception:
                self._retry.add(session.session_id)
                raise

            self._retry.discard(session.session_id)
            return message_id

    async def refresh(self):
        # 保存済みの案内を読み、終了した画面を停止する。
        async with self._lock:
            if self._closed:
                return

            sessions = await self._welcome.open_sessions()
            active = {
                session.session_id: session
                for session in sessions
                if session.state == "active"
            }

            for session_id in tuple(self._entries):
                if session_id not in active:
                    self._discard(session_id)

            targets = [
                session
                for session_id, session in active.items()
                if session_id not in self._entries or session_id in self._retry
            ]

        # ensure自身が同じロックを使うので、ここではロックを解放する。
        for session in targets:
            try:
                await self.ensure(session)
            except Exception as error:
                log_event(
                    "welcome",
                    "questionnaire_panel_restore_failed",
                    guild_id=self._guild_id,
                    level=logging.ERROR,
                    error=error,
                )

    async def close(self):
        # 回答保存や退出処理と競合しないよう、同じロックで終了する。
        async with self._lock:
            self._closed = True
            for session_id in tuple(self._entries):
                self._discard(session_id)
