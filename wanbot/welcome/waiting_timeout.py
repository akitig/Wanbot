"""本人期限72時間の承認依頼と、承認前の有効性確認。"""

from wanbot.welcome.interview_gate import InterviewNotReady
from wanbot.welcome.kick_execution import KickTargetChanged
from wanbot.welcome.waiting_limits import DEFAULT_LIMITS, duration_text


class WaitingTimeout:
    def __init__(self, *, monitor):
        self._monitor = monitor
        self._controller = monitor._controller
        self._review = self._controller._kick_review

    def _scope(self, record):
        context = self._controller._context
        if (
            record["guild_id"] != context.guild_id
            or record["session_id"] != context.session_id
            or record["user_id"] != context.user_id
            or record["channel_id"] != context.channel_id
        ):
            raise KickTargetChanged("期限切れ依頼の対象が一致しません。")

    @staticmethod
    def _still_due(clock, action, record):
        expected = record["waiting_timeout"]
        return (
            clock is not None
            and action == "review"
            and clock["owner"] == "applicant"
            and clock["epoch"] == expected["epoch"]
            and clock["progress_key"] == expected["progress_key"]
        )

    async def validate_locked(self, record):
        """キック実行側から、Welcome共通ロック内で呼ぶ。"""
        self._scope(record)
        if "waiting_timeout" not in record:
            raise InterviewNotReady("本人期限の記録がありません。")
        if not self._monitor.available():
            raise InterviewNotReady("現在、本人期限を再確認できません。")

        clock, action = await self._monitor.candidate_locked()
        if clock is None or clock["owner"] == "unknown":
            # 取得失敗は状態変更と断定せず、依頼を残して実行だけ止める。
            raise InterviewNotReady(
                "進行状態を取得できません。キックは実行していません。"
            )
        if not self._still_due(clock, action, record):
            raise KickTargetChanged(
                "手続きの進行または期限が変わりました。"
                "この古い依頼ではキックしません。"
            )
        return True

    async def _publish(self, record):
        key = (record["request_id"], record["status"])
        if key in self._review._posted:
            return
        channel, guild_config = await self._review._channel()
        await self._review._publish(record, channel, guild_config)

    async def refresh(self):
        controller = self._controller
        if not self._monitor.available():
            return

        async with controller._lock:
            clock, action = await self._monitor.candidate_locked()
            if clock is None or clock["owner"] == "unknown":
                return

            context = controller._context
            repository = self._review._repository
            records = await repository.for_session(context.session_id)

            # 以前の期限の依頼が残っていれば、実行できない状態へ移す。
            for record in records:
                self._scope(record)
                if (
                    "waiting_timeout" in record
                    and record["status"] in ("pending", "approved")
                    and not self._still_due(clock, action, record)
                ):
                    record = await repository.transition(
                        record["request_id"],
                        expected=record["status"],
                        target="obsolete",
                    )
                    await self._publish(record)

            if action != "review":
                return

            records = await repository.for_session(context.session_id)
            expected = {
                "epoch": clock["epoch"],
                "progress_key": clock["progress_key"],
            }
            matching = [
                record for record in records
                if record.get("waiting_timeout") == expected
            ]
            if len(matching) > 1:
                raise InterviewNotReady("同じ本人期限の依頼が複数あります。")
            if matching:
                # キャンセル済み・無効化済みでも、同じ期限では再作成しない。
                await self._publish(matching[0])
                return

            if any(
                record["status"] in (
                    "pending", "approved", "executing", "completed"
                )
                for record in records
            ):
                return

            channel, guild_config = await self._review._channel()
            member = await channel.guild.fetch_member(context.user_id)
            if member.joined_at is None:
                raise InterviewNotReady("今回のサーバー参加日時を確認できません。")

            # チャンネル・メンバー取得後、作成直前にも進捗を確認する。
            latest, latest_action = await self._monitor.candidate_locked()
            probe = {"waiting_timeout": expected}
            if not self._still_due(latest, latest_action, probe):
                return

            await controller._session()
            bot = controller._client.user
            if bot is None:
                raise InterviewNotReady("Botの情報を確認できません。")

            clock_repository = getattr(self._monitor, "_repository", None)
            limits = getattr(clock_repository, "limits", DEFAULT_LIMITS)
            duration = duration_text(limits.timeout_seconds)
            record = await repository.create(
                context=context,
                joined_at=member.joined_at,
                actor_id=bot.id,
                reason=(
                    f"参加手続きで本人の対応待ちが{duration}に達しました。"
                    "承認時に進行状態と期限を再確認します。"
                ),
                waiting_timeout=expected,
                now=controller._clock(),
            )
            await self._review._publish(record, channel, guild_config)
