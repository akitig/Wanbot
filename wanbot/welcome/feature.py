from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING

import discord

from wanbot.config import AppConfig, ConfigError, GuildConfig
from wanbot.logging_config import log_event
from wanbot.welcome.archive import WelcomeArchiveChannel
from wanbot.welcome.channels import WelcomeChannelError, WelcomeChannels
from wanbot.welcome.commands import register_welcome_command
from wanbot.welcome.lifecycle import WelcomeLifecycle
from wanbot.welcome.questionnaire_manager import WelcomeQuestionnaires
from wanbot.welcome.questionnaire_panel_repository import (
    QuestionnairePanelRepository,
)
from wanbot.welcome.questionnaire_repository import QuestionnaireRepository
from wanbot.welcome.questionnaire_result import QuestionnaireResultRepository
from wanbot.welcome.repository import WelcomeRepository, WelcomeSession
from wanbot.welcome.rules import RulesReactionChecker
from wanbot.welcome.meeting_controller import MeetingInterviewController
from wanbot.welcome.meeting_repository import MeetingPlanRepository
from wanbot.welcome.meeting_voice import MeetingVoice
from wanbot.welcome.meeting_voice_cleanup import MeetingVoiceCleanup
from wanbot.welcome.admission_repository import AdmissionRepository
from wanbot.welcome.admission_service import AdmissionService
from wanbot.welcome.kick_repository import KickRequestRepository
from wanbot.welcome.kick_review import KickReview
from wanbot.welcome.no_show_repository import NoShowRepository
from wanbot.welcome.no_show_binding import NoShowBinding
from wanbot.welcome.waiting_clock_repository import WaitingClockRepository
from wanbot.welcome.waiting_state_reader import WaitingStateReader
from wanbot.welcome.waiting_monitor import WaitingMonitor
from wanbot.welcome.waiting_reminder import WaitingReminder
from wanbot.welcome.waiting_timeout import WaitingTimeout
from wanbot.welcome.timeout_notice import TimeoutNotice
from wanbot.welcome.waiting_limits import limits_for_guild
from wanbot.welcome.interview_gate import InterviewGate, InterviewNotReady
from wanbot.welcome.interview_repository import InterviewRequestRepository
from wanbot.valorant.questions import load_questions
from wanbot.valorant.scoring import DiagnosisScorer
from wanbot.welcome.valorant_panel import (
    WelcomeDiagnosisPanel,
    WelcomeDiagnosisPanelRepository,
)
from wanbot.welcome.valorant_service import WelcomeDiagnosisService

if TYPE_CHECKING:
    from wanbot.bot import WanbotClient


from wanbot.welcome.staff_reminder_binding import StaffReminderBinding
from wanbot.welcome.staff_reminder_repository import StaffReminderRepository


from wanbot.welcome.schedule_change_binding import ScheduleChangeBinding
from wanbot.welcome.schedule_change_repository import ScheduleChangeRepository


from wanbot.welcome.age_review import AgeReview


ARCHIVE_RETRY_SECONDS = 30.0


class WelcomeFeature:
    def __init__(
        self,
        client: WanbotClient,
        config: AppConfig,
        guild: GuildConfig,
    ) -> None:
        if not guild.feature_enabled("welcome"):
            raise ConfigError("Welcome機能が有効になっていません。")

        self._client = client
        self._config = config
        self._guild_id = guild.guild_id
        self._started = False
        self._closed = False
        self._registered = False
        self._pending: set[asyncio.Task] = set()
        self._worker: asyncio.Task | None = None
        self._reconcile_needed = True
        self._guild_config = guild
        self._diagnosis_resources = AsyncExitStack()

        self._repository = WelcomeRepository(config, guild.guild_id)
        self._channels = WelcomeChannels(
            client=client,
            guild_config=guild,
            repository=self._repository,
        )
        self._archive = WelcomeArchiveChannel(
            client=client,
            guild_config=guild,
        )
        self._lifecycle = WelcomeLifecycle(
            guild_id=guild.guild_id,
            repository=self._repository,
            channels=self._channels,
            archive=self._archive,
        )
        self._question_repository = QuestionnaireRepository(
            config, guild.guild_id
        )
        self._question_panel_repository = QuestionnairePanelRepository(
            config, guild.guild_id
        )
        self._question_result_repository = QuestionnaireResultRepository(
            config, guild.guild_id
        )
        self._diagnosis_panel_repository = WelcomeDiagnosisPanelRepository(
            config, guild.guild_id
        )
        self._diagnosis_resources.push_async_callback(
            self._diagnosis_panel_repository.close
        )
        self._interview_repository = InterviewRequestRepository(
            config, guild.guild_id
        )
        self._diagnosis_resources.push_async_callback(
            self._interview_repository.close
        )
        self._meeting_repository = MeetingPlanRepository(
            config, guild.guild_id
        )
        self._diagnosis_resources.push_async_callback(
            self._meeting_repository.close
        )
        self._meeting_voice = MeetingVoice(
            client=client, config=config, guild_id=guild.guild_id
        )
        self._diagnosis_resources.push_async_callback(
            self._meeting_voice.close
        )
        self._admissions = AdmissionRepository(config, guild.guild_id)
        self._diagnosis_resources.push_async_callback(
            self._admissions.close
        )
        self._admission_service = AdmissionService(
            client=client,
            repository=self._admissions,
            welcome_repository=self._repository,
            archive=self._archive,
            lock=self._lifecycle.operation_lock,
        )
        self._voice_cleanup = MeetingVoiceCleanup(
            client=client, config=config, guild_id=guild.guild_id,
            voices=self._meeting_voice,
            welcome_repository=self._repository,
            is_available=self._available,
        )
        self._diagnosis_resources.push_async_callback(
            self._voice_cleanup.close
        )
        self._kick_repository = KickRequestRepository(config, guild.guild_id)
        self._diagnosis_resources.push_async_callback(
            self._kick_repository.close
        )
        self._kick_review = KickReview(
            client=client, config=config, guild_id=guild.guild_id,
            repository=self._kick_repository,
            admissions=self._admissions,
            lock=self._lifecycle.operation_lock,
            welcome_repository=self._repository,
            archive=self._archive,
        )
        self._diagnosis_resources.callback(self._kick_review.close)
        self._age_review = AgeReview(
            client=client,
            guild_id=guild.guild_id,
            welcome=self._repository,
            questions=self._question_repository,
            admissions=self._admissions,
            review=self._kick_review,
            is_available=self._available,
        )
        self._kick_review._executor.set_age_validator(
            self._age_review.validate_locked
        )
        self._no_show_repository = NoShowRepository(config, guild.guild_id)
        self._diagnosis_resources.push_async_callback(
            self._no_show_repository.close
        )
        self._no_show_bindings = {}
        self._waiting_clocks = WaitingClockRepository(
            config, guild.guild_id, limits=limits_for_guild(guild)
        )
        self._diagnosis_resources.push_async_callback(
            self._waiting_clocks.close
        )
        self._waiting_monitors = {}
        self._waiting_reminders = {}
        self._waiting_timeouts = {}
        self._staff_reminder_repository = StaffReminderRepository(
            config, guild.guild_id
        )
        self._diagnosis_resources.push_async_callback(
            self._staff_reminder_repository.close
        )
        self._staff_reminders = {}
        self._schedule_changes = ScheduleChangeRepository(
            config, guild.guild_id
        )
        self._diagnosis_resources.push_async_callback(
            self._schedule_changes.close
        )
        self._schedule_bindings = {}
        self._voice_cleanup.set_schedule_changes(self._schedule_changes)
        self._timeout_notice = TimeoutNotice(config, guild.guild_id)
        self._diagnosis_resources.push_async_callback(
            self._timeout_notice.close
        )
        self._kick_review._executor.set_timeout_validator(
            self._validate_waiting_timeout
        )
        self._kick_review._executor.set_timeout_notice(self._timeout_notice)
        self._questionnaires = WelcomeQuestionnaires(
            client=client,
            guild_id=guild.guild_id,
            welcome_repository=self._repository,
            questionnaire_repository=self._question_repository,
            panel_repository=self._question_panel_repository,
            result_repository=self._question_result_repository,
            diagnosis_factory=self._make_diagnosis_panel,
            interview_factory=self._make_interview,
            rules_emoji=guild.rules_emoji,
            rules_checker=RulesReactionChecker(
                client=client,
                guild_config=guild,
            ),
            lock=self._lifecycle.operation_lock,
            is_available=self._available,
        )

    def _make_interview(self, context, diagnosis):
        if diagnosis is None:
            raise RuntimeError("Welcome diagnosis is not configured")

        gate = InterviewGate(
            context=context,
            welcome_repository=self._repository,
            questionnaire_repository=self._question_repository,
            rules_checker=RulesReactionChecker(
                client=self._client,
                guild_config=self._guild_config,
            ),
            load_diagnosis=diagnosis.load_progress,
        )
        controller = MeetingInterviewController(
            config=self._config,
            meeting_repository=self._meeting_repository,
            meeting_voice=self._meeting_voice,
            admissions=self._admissions,
            admission_service=self._admission_service,
            admission_questions=self._question_repository,
            admission_diagnosis=diagnosis.load_progress,
            client=self._client,
            context=context,
            welcome_repository=self._repository,
            repository=self._interview_repository,
            gate=gate,
            lock=self._lifecycle.operation_lock,
        )

        controller._kick_review = self._kick_review
        binding = NoShowBinding(
            controller=controller,
            repository=self._no_show_repository,
        )
        controller._no_show = binding
        self._no_show_bindings[context.session_id] = binding
        reader = WaitingStateReader(
            controller=controller,
            questions=self._question_repository,
            diagnosis=diagnosis,
            rules=RulesReactionChecker(
                client=self._client,
                guild_config=self._guild_config,
            ),
            no_shows=self._no_show_repository,
        )
        self._waiting_monitors[context.session_id] = WaitingMonitor(
            controller=controller,
            reader=reader,
            repository=self._waiting_clocks,
        )
        self._waiting_reminders[context.session_id] = WaitingReminder(
            monitor=self._waiting_monitors[context.session_id]
        )
        self._waiting_timeouts[context.session_id] = WaitingTimeout(
            monitor=self._waiting_monitors[context.session_id]
        )
        self._staff_reminders[context.session_id] = StaffReminderBinding(
            controller=controller,
            repository=self._staff_reminder_repository,
        )
        controller._schedule_change = ScheduleChangeBinding(
            controller=controller, repository=self._schedule_changes
        )
        self._schedule_bindings[context.session_id] = (
            controller._schedule_change
        )
        return controller

    def _make_diagnosis_panel(self, context):
        scorer = DiagnosisScorer(load_questions())
        service = WelcomeDiagnosisService(
            self._config,
            self._guild_id,
            scorer,
            welcome_session_id=context.session_id,
        )
        self._diagnosis_resources.push_async_callback(service.close)
        return WelcomeDiagnosisPanel(
            client=self._client,
            context=context,
            welcome_repository=self._repository,
            questionnaire_repository=self._question_repository,
            panel_repository=self._diagnosis_panel_repository,
            service=service,
            lock=self._lifecycle.operation_lock,
            timeout_seconds=self._guild_config.valorant_timeout_seconds,
        )

    def _available(self) -> bool:
        return (
            self._started
            and not self._closed
            and self._client.feature_available(self._guild_id, "welcome")
        )

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("Welcome feature is closed")
        if self._started:
            return

        try:
            register_welcome_command(
                self._client.tree,
                self._config,
                self._guild_id,
                is_available=self._available,
                create_welcome=self.create,
            )
            self._registered = True
        except BaseException:
            await self.stop()
            raise

        self._started = True
        try:
            await self._questionnaires.refresh()
        except Exception as error:
            log_event(
                "welcome",
                "questionnaire_restore_failed",
                guild_id=self._guild_id,
                level=logging.ERROR,
                error=error,
            )

        self._worker = asyncio.create_task(
            self._archive_worker(),
            name=f"welcome-archive-worker-{self._guild_id}",
        )

    async def _run(self, operation, *, name):
        task = asyncio.create_task(
            operation,
            name=f"welcome-{name}-{self._guild_id}",
        )
        self._pending.add(task)
        task.add_done_callback(self._completed)
        return await asyncio.shield(task)

    async def create(self, member: discord.Member) -> WelcomeSession:
        if not self._available():
            raise WelcomeChannelError("Welcome is unavailable")

        return await self._run(
            self._create_with_questions(member),
            name="create",
        )

    async def _create_with_questions(self, member):
        session = await self._lifecycle.ensure(member)
        await self._questionnaires.ensure(session)
        return session

    async def member_left(self, member) -> WelcomeSession | None:
        if not self._available():
            raise WelcomeChannelError("Welcome is unavailable")

        return await self._run(
            self._lifecycle.member_left(member),
            name="archive",
        )

    def reset_voice_cleanup(self):
        self._voice_cleanup.reset()

    def handle_voice_change(self, before, after):
        self._voice_cleanup.voice_changed(before, after)

    async def handle_welcome_message(self, message):
        if self._available():
            await self._questionnaires.message_posted(message)

    async def notify_rules_change(self, user_id=None):
        if not self._available():
            return
        await self._run(
            self._questionnaires.refresh_checklists(user_id),
            name="rules-checklist-refresh",
        )

    async def retry_archives(self):
        if not self._available():
            return None

        return await self._run(
            self._lifecycle.resume_pending(),
            name="archive-retry",
        )

    async def reconcile_members(self):
        if not self._available():
            return None

        guild = self._client.get_guild(self._guild_id)
        return await self._run(
            self._lifecycle.reconcile_members(guild),
            name="membership-reconciliation",
        )

    async def _refresh_no_shows(self):
        if not self._available():
            return
        for binding in tuple(self._no_show_bindings.values()):
            try:
                await binding.refresh()
            except Exception as error:
                binding._log(error)

    async def _validate_waiting_timeout(self, record):
        if not self._available():
            raise InterviewNotReady("現在、本人期限を再確認できません。")
        service = self._waiting_timeouts.get(record["session_id"])
        if service is None:
            raise InterviewNotReady(
                "参加手続きの復旧待ちです。少し待って操作してください。"
            )
        return await service.validate_locked(record)

    async def _refresh_waiting_clocks(self):
        if not self._available():
            return
        for session_id, monitor in tuple(self._waiting_monitors.items()):
            if monitor._controller._closed:
                self._waiting_monitors.pop(session_id, None)
                self._waiting_reminders.pop(session_id, None)
                self._waiting_timeouts.pop(session_id, None)
                continue
            try:
                await monitor.refresh()
                await self._waiting_reminders[session_id].refresh()
                await self._waiting_timeouts[session_id].refresh()
            except Exception as error:
                log_event(
                    "welcome",
                    "waiting_clock_update_failed",
                    guild_id=self._guild_id,
                    level=logging.ERROR,
                    error=error,
                )

    async def _refresh_staff_reminders(self):
        if not self._available():
            return
        for session_id, binding in tuple(self._staff_reminders.items()):
            if binding._controller._closed:
                self._staff_reminders.pop(session_id, None)
                continue
            try:
                await binding.refresh()
            except Exception as error:
                log_event(
                    "welcome",
                    "staff_reminder_failed",
                    guild_id=self._guild_id,
                    level=logging.ERROR,
                    error=error,
                )

    async def _refresh_schedule_changes(self):
        if not self._available():
            return
        for session_id, binding in tuple(self._schedule_bindings.items()):
            if binding._controller._closed:
                self._schedule_bindings.pop(session_id, None)
                continue
            try:
                await binding.refresh()
            except Exception as error:
                log_event(
                    "welcome", "schedule_change_resume_failed",
                    guild_id=self._guild_id,
                    level=logging.ERROR, error=error,
                )

    async def _refresh_age_reviews(self):
        if not self._available():
            return
        async with self._lifecycle.operation_lock:
            sessions = await self._repository.open_sessions()
            for session in sessions:
                if not self._available():
                    return
                try:
                    await self._age_review.refresh_session_locked(session)
                except Exception as error:
                    log_event(
                        "welcome", "age_review_failed",
                        guild_id=self._guild_id,
                        level=logging.ERROR, error=error,
                    )

    async def _archive_worker(self):
        while not self._closed:
            await asyncio.sleep(ARCHIVE_RETRY_SECONDS)

            try:
                await self._run(
                    self._voice_cleanup.tick(), name="meeting-vc-cleanup"
                )
            except Exception:
                self._voice_cleanup.reset()

            if self._available():
                try:
                    await self._run(
                        self._kick_review.restore(), name="kick-review-restore"
                    )
                except Exception:
                    pass
                try:
                    await self._run(
                        self._admission_service.resume(),
                        name="admission-resume",
                    )
                except Exception:
                    # 保存先などの一時的な失敗は次回再試行する。
                    pass
                try:
                    await self._run(
                        self._questionnaires.refresh(),
                        name="questionnaire-refresh",
                    )
                except Exception:
                    # 保存データ読み込みなどの失敗は次の周期で再試行。
                    pass

            if self._available():
                await self._run(
                    self._refresh_schedule_changes(),
                    name="schedule-change-refresh",
                )
                await self._run(
                    self._refresh_age_reviews(), name="age-review-refresh"
                )
                await self._run(
                    self._refresh_no_shows(), name="no-show-refresh"
                )
                await self._run(
                    self._refresh_waiting_clocks(), name="waiting-clock-refresh"
                )
                await self._run(
                    self._refresh_staff_reminders(), name="staff-reminder-refresh"
                )

            try:
                result = await self.retry_archives()
            except Exception:
                # 詳細は追跡中のタスクの完了処理で安全に記録する。
                # 一度失敗しても、次回の再試行を続ける。
                continue

            if result is not None and result[0] > 0:
                log_event(
                    "welcome",
                    "pending_archives_completed",
                    guild_id=self._guild_id,
                )

            if self._reconcile_needed:
                try:
                    result = await self.reconcile_members()
                except Exception:
                    # 起動後の確認も、失敗時は次の周期で再試行する。
                    continue

                if result is not None and result[1] == 0:
                    self._reconcile_needed = False
                    log_event(
                        "welcome",
                        "membership_reconciliation_completed",
                        guild_id=self._guild_id,
                    )

    def _completed(self, task: asyncio.Task) -> None:
        self._pending.discard(task)
        if task.cancelled():
            return

        error = task.exception()
        if error is not None:
            log_event(
                "welcome",
                "operation_failed",
                guild_id=self._guild_id,
                level=logging.ERROR,
                error=error,
            )

    async def stop(self) -> None:
        if self._closed:
            return

        self._closed = True
        self._started = False

        try:
            if self._registered:
                self._client.tree.remove_command(
                    "welcome",
                    guild=discord.Object(id=self._guild_id),
                )
                self._registered = False
        finally:
            try:
                if self._worker is not None:
                    self._worker.cancel()
                    await asyncio.gather(
                        self._worker,
                        return_exceptions=True,
                    )

                pending = tuple(self._pending)
                if pending:
                    await asyncio.shield(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            finally:
                try:
                    try:
                        await self._questionnaires.close()
                    finally:
                        await self._diagnosis_resources.aclose()
                finally:
                    try:
                        await self._question_repository.close()
                    finally:
                        try:
                            await self._question_panel_repository.close()
                        finally:
                            try:
                                await self._question_result_repository.close()
                            finally:
                                await self._repository.close()
