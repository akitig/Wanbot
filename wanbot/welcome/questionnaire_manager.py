"""案内中のWelcomeに質問パネルを用意し、再起動後に復旧する。"""

import logging
import asyncio

import discord

from wanbot.logging_config import log_event
from wanbot.welcome.input_activity import InputActivity
from wanbot.welcome.checklist_binding import bind_checklist
from wanbot.welcome.questionnaire_controller import QuestionnaireController
from wanbot.welcome.questionnaire_forms import QuestionnaireContext
from wanbot.welcome.questionnaire_panel import QuestionnairePanel
from wanbot.welcome.questionnaire_result import QuestionnaireResult


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
        result_repository=None,
        rules_checker=None,
        diagnosis_factory=None,
        interview_factory=None,
        rules_emoji="👍",
    ):
        self._client = client
        self._guild_id = guild_id
        self._welcome = welcome_repository
        self._questions = questionnaire_repository
        self._panels = panel_repository
        self._results = result_repository
        self._rules_checker = rules_checker
        self._rules_emoji = rules_emoji
        self._diagnosis_factory = diagnosis_factory
        self._diagnoses = {}
        self._interview_factory = interview_factory
        self._interviews = {}
        self._lock = lock
        self._is_available = is_available
        self._entries = {}
        self._retry = set()
        self._closed = False
        self._sticky_tasks = {}

    def _available(self):
        return not self._closed and self._is_available()

    def _discard(self, session_id):
        interview = self._interviews.pop(session_id, None)
        if interview is not None:
            interview.close()
        diagnosis = self._diagnoses.pop(session_id, None)
        if diagnosis is not None:
            diagnosis.close()
        entry = self._entries.pop(session_id, None)
        if entry is not None:
            controller, panel, result = entry
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
                    activity=InputActivity(),
                )
                result = (
                    QuestionnaireResult(
                        client=self._client,
                        context=context,
                        repository=self._results,
                    )
                    if self._results is not None
                    else None
                )
                diagnosis = None
                if self._diagnosis_factory is not None:
                    diagnosis = self._diagnosis_factory(context)
                    self._diagnoses[session.session_id] = diagnosis

                interview = None
                if self._interview_factory is not None:
                    interview = self._interview_factory(context, diagnosis)
                    self._interviews[session.session_id] = interview

                controller = QuestionnaireController(
                    context=context,
                    welcome_repository=self._welcome,
                    questionnaire_repository=self._questions,
                    lock=self._lock,
                    on_completed=result.publish if result is not None else None,
                    rules_checker=self._rules_checker,
                    checklist_mode=self._rules_checker is not None,
                    on_interview=(
                        interview.begin if interview is not None else None
                    ),
                    on_diagnosis=(
                        diagnosis.begin if diagnosis is not None else None
                    ),
                )
                if self._rules_checker is not None:
                    panel = bind_checklist(
                        self, context, controller, diagnosis, interview
                    )
                else:
                    panel = QuestionnairePanel(
                        client=self._client,
                        context=context,
                        repository=self._panels,
                        view=controller.start_view,
                    )
                self._entries[session.session_id] = (
                    controller, panel, result
                )

            controller, panel, result = self._entries[session.session_id]
            try:
                message_id = await panel.ensure()
                if result is not None:
                    snapshot = await self._questions.get(session.session_id)
                    if (
                        snapshot is not None
                        and snapshot.progress.status == "completed"
                    ):
                        await result.publish(snapshot)
            except discord.NotFound as error:
                self._retry.add(session.session_id)
                if error.code != 10003:
                    raise

                # 投稿の不存在と混同しないよう、チャンネル自体を再確認する。
                try:
                    await self._client.fetch_channel(current.channel_id)
                except discord.NotFound as confirmation:
                    if confirmation.code != 10003:
                        raise

                    await self._welcome.close_session(
                        current.session_id,
                        reason="channel_deleted",
                    )
                    self._discard(current.session_id)
                    log_event(
                        "welcome",
                        "missing_channel_session_closed",
                        guild_id=self._guild_id,
                        level=logging.WARNING,
                    )
                    return None

                # チャンネルが取得できた場合は終了させず、次回再試行する。
                raise
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
                if (
                    session_id not in self._entries
                    or session_id in self._retry
                    or (
                        self._rules_checker is not None
                        and self._available()
                    )
                )
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

    async def message_posted(self, message):
        async with self._lock:
            if self._closed or self._rules_checker is None:
                return
            session = None
            for candidate in await self._welcome.open_sessions():
                if (
                    candidate.state == "active"
                    and candidate.channel_id == message.channel.id
                ):
                    session = candidate
                    break
            if session is None:
                return

            saved_id = await self._panels.get(session.session_id)
            if message.id == saved_id:
                # チェックリスト自身の投稿で再移動しない。
                return
            if session.session_id in self._sticky_tasks:
                return

            async def update():
                try:
                    await asyncio.sleep(2)
                    await self.ensure(session)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    log_event(
                        "welcome",
                        "sticky_checklist_failed",
                        guild_id=self._guild_id,
                        level=logging.ERROR,
                        error=error,
                    )
                finally:
                    self._sticky_tasks.pop(session.session_id, None)

            self._sticky_tasks[session.session_id] = asyncio.create_task(
                update()
            )

    async def refresh_checklists(self, user_id=None):
        async with self._lock:
            if self._closed:
                return
            sessions = await self._welcome.open_sessions()
            targets = [
                session
                for session in sessions
                if session.state == "active"
                and (user_id is None or session.user_id == user_id)
            ]

        for session in targets:
            await self.ensure(session)

    async def close(self):
        tasks = tuple(self._sticky_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._sticky_tasks.clear()
        # 回答保存や退出処理と競合しないよう、同じロックで終了する。
        async with self._lock:
            self._closed = True
            for session_id in tuple(self._entries):
                self._discard(session_id)
