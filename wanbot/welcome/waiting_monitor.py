"""Welcomeの進行状態を読み、本人待ちの時計を更新する。"""

import logging

from wanbot.logging_config import log_event
from wanbot.welcome.waiting_clock_repository import due_action
from wanbot.welcome.waiting_limits import DEFAULT_LIMITS
from wanbot.welcome.waiting_policy import WaitingDecision


class WaitingMonitor:
    def __init__(self, *, controller, reader, repository):
        self._controller = controller
        self._reader = reader
        self._repository = repository

    def available(self):
        controller = self._controller
        return (
            not controller._closed
            and controller._context.is_available()
        )

    async def observe_locked(self):
        """共通ロック内で使用する。通知やキックは実行しない。"""
        if not self.available():
            return None

        controller = self._controller
        context = controller._context
        try:
            observation = await self._reader.read_locked()
            # 判定時の不正な状態も、取得失敗と同じく保留にする。
            decision = observation.decision
            progress_key = observation.progress_key
        except Exception as error:
            log_event(
                "welcome",
                "waiting_state_read_failed",
                guild_id=context.guild_id,
                level=logging.WARNING,
                error=error,
            )
            decision = WaitingDecision(
                "unknown", "state_read_failed"
            )
            # unknownの観測では、保存済みの進捗識別子は変更されない。
            progress_key = "unavailable"

        # Discordへの通信中に機能が停止した場合は更新しない。
        if not self.available():
            return None

        return await self._repository.observe(
            context.session_id,
            decision=decision,
            progress_key=progress_key,
            now=controller._clock(),
        )

    async def refresh(self):
        controller = self._controller
        if not self.available():
            return None
        async with controller._lock:
            return await self.observe_locked()

    async def candidate_locked(self):
        """将来の通知処理用。必ず状態を再取得してから期限を判定する。"""
        record = await self.observe_locked()
        if record is None:
            return None, None
        return record, due_action(
            record,
            now=self._controller._clock(),
            limits=getattr(self._repository, "limits", DEFAULT_LIMITS),
        )
