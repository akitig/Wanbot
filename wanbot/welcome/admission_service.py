"""保存済みの参加承認を、ロール付与からLog移動まで進める。"""

import logging

from wanbot.logging_config import log_event
from wanbot.welcome.admission_roles import grant_admission_roles
from wanbot.welcome.interview_gate import InterviewNotReady


class AdmissionService:
    def __init__(
        self, *, client, repository, welcome_repository, archive, lock
    ):
        self._client = client
        self._repository = repository
        self._welcome = welcome_repository
        self._archive = archive
        self._lock = lock

    async def finish_locked(self, record):
        """呼び出し元がWelcome共通ロックを取得してから呼ぶ。"""
        session_id = record["session_id"]
        proposal_id = record["proposal_id"]
        current = await self._repository.get(session_id)
        if current is None or current["proposal_id"] != proposal_id:
            raise InterviewNotReady("保存済みの参加承認を確認できません。")
        record = current

        if record["phase"] == "completed":
            return

        history = await self._welcome.history(record["user_id"])
        session = next(
            (item for item in history if item.session_id == session_id),
            None,
        )
        if session is None or session.channel_id != record["channel_id"]:
            raise InterviewNotReady("承認対象のWelcomeを確認できません。")

        if session.state == "closed":
            if (
                session.closed_reason != "completed"
                or record["phase"] != "roles_verified"
            ):
                raise InterviewNotReady("このWelcomeは別の理由で終了しています。")
            await self._repository.mark_completed(session_id, proposal_id)
            return

        if session.state == "archiving":
            if (
                session.closed_reason != "completed"
                or record["phase"] != "roles_verified"
            ):
                raise InterviewNotReady("別の終了処理が進行しています。")
        elif session.state == "active":
            active = await self._welcome.get_active(record["user_id"])
            if active is None or active.session_id != session_id:
                raise InterviewNotReady("現在のWelcomeと承認対象が一致しません。")

            # 再開時にも、必要なロールが付いていることを確認する。
            await grant_admission_roles(
                client=self._client,
                guild_id=record["guild_id"],
                user_id=record["user_id"],
                role_ids=record["role_ids"],
            )
            await self._repository.mark_roles_verified(session_id, proposal_id)
            session = await self._welcome.begin_archive(
                session_id, reason="completed"
            )
        else:
            raise InterviewNotReady("このWelcomeは参加承認を実行できる状態ではありません。")

        channel_id = await self._archive.archive(session)
        if channel_id is not None:
            await self._welcome.record_archive_channel(session_id, channel_id)

        await self._welcome.close_session(session_id, reason="completed")
        await self._repository.mark_completed(session_id, proposal_id)

    async def resume(self):
        """保存済みの承認だけを再開する。新しい承認は行わない。"""
        async with self._lock:
            for record in await self._repository.pending():
                try:
                    await self.finish_locked(record)
                except Exception as error:
                    log_event(
                        "welcome",
                        "admission_resume_failed",
                        guild_id=record["guild_id"],
                        level=logging.ERROR,
                        error=error,
                    )
