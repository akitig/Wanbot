"""明示的な管理者承認後にキックし、退出確認後にWelcomeを終了する。"""

import discord

from wanbot.logging_config import log_event

from wanbot.welcome.interview_gate import InterviewNotReady
from wanbot.welcome.kick_repository import (
    KickRequestConflict,
    same_membership,
)


class KickTargetChanged(InterviewNotReady):
    pass


class KickExecution:
    def __init__(
        self, *, client, guild_id, repository, admissions,
        welcome_repository, archive,
    ):
        self._client = client
        self._guild_id = guild_id
        self._repository = repository
        self._admissions = admissions
        self._welcome = welcome_repository
        self._archive = archive
        self._timeout_validator = None
        self._age_validator = None
        self._timeout_notice = None

    def set_age_validator(self, validator):
        if not callable(validator):
            raise TypeError("Age validator must be callable")
        self._age_validator = validator

    async def _check_age_review(self, record):
        if "age_review" not in record:
            return
        if record["age_review"] is not True:
            raise InterviewNotReady("年齢確認の識別情報が不正です。")
        validator = getattr(self, "_age_validator", None)
        if validator is None:
            raise InterviewNotReady(
                "年齢回答の再確認処理を利用できません。"
                "キックは実行していません。"
            )
        if await validator(record) is not True:
            raise InterviewNotReady(
                "年齢回答を確認できません。キックは実行していません。"
            )

    def set_timeout_validator(self, validator):
        """期限切れ依頼を承認するときの最新状態確認を設定する。"""
        if not callable(validator):
            raise TypeError("Timeout validator must be callable")
        self._timeout_validator = validator

    async def _check_waiting_timeout(self, record):
        if "waiting_timeout" not in record:
            return
        validator = getattr(self, "_timeout_validator", None)
        if validator is None:
            raise InterviewNotReady(
                "本人期限の再確認処理を利用できません。"
                "キックは実行していません。"
            )
        # False・Noneも許可扱いにしない。
        # 状態変更は接続先でKickTargetChangedとして通知する。
        if await validator(record) is not True:
            raise InterviewNotReady(
                "本人期限を確認できません。キックは実行していません。"
            )

    def set_timeout_notice(self, notice):
        self._timeout_notice = notice

    async def _notify_before_kick(self, record, member):
        notice = getattr(self, "_timeout_notice", None)
        if notice is None:
            raise InterviewNotReady(
                "キック前の通知処理を利用できません。キックは実行していません。"
            )
        result = await notice.notify(record, member)
        events = {
            "sent": "timeout_dm_sent",
            "failed": "timeout_dm_failed",
            "unknown": "timeout_dm_result_unknown",
            "attempted": "timeout_dm_previous_attempt_unknown",
        }
        event = events.get(result["status"])
        if event is None:
            raise InterviewNotReady("キック前の通知結果を確認できません。")
        log_event("welcome", event, guild_id=self._guild_id)

    def _guild(self):
        guild = self._client.get_guild(self._guild_id)
        if guild is None or guild.unavailable or self._client.user is None:
            raise InterviewNotReady("サーバーを確認できません。")
        return guild

    async def _member(self, user_id):
        try:
            return await self._guild().fetch_member(user_id)
        except discord.NotFound as error:
            if error.code == 10007:
                return None
            raise

    async def validate_target(self, record):
        if record["guild_id"] != self._guild_id:
            raise KickTargetChanged("確認依頼のサーバーが一致しません。")
        if await self._admissions.get(record["session_id"]) is not None:
            raise KickTargetChanged("対象者はすでに参加承認されています。")

        session = await self._welcome.get_active(record["user_id"])
        if (
            session is None
            or session.state != "active"
            or session.channel_id != record["channel_id"]
        ):
            raise KickTargetChanged("現在のWelcomeを確認できません。")

        guild = self._guild()
        bot = await guild.fetch_member(self._client.user.id)
        roles = {role.id: role for role in await guild.fetch_roles()}
        member = await self._member(record["user_id"])
        if member is None:
            raise KickTargetChanged("対象者はすでに退出しています。")

        if not same_membership(
            record,
            guild_id=guild.id,
            user_id=member.id,
            session_id=session.session_id,
            joined_at=member.joined_at,
        ):
            raise KickTargetChanged(
                "参加日時またはWelcomeが変わりました。古い依頼ではキックしません。"
            )
        if member.bot or member.id == guild.owner_id:
            raise InterviewNotReady("この対象者はキックできません。")
        if not bot.guild_permissions.kick_members:
            raise InterviewNotReady("Botにキック権限がありません。")

        bot_roles = [roles[role.id] for role in bot.roles if role.id in roles]
        target_roles = [
            roles[role.id] for role in member.roles if role.id in roles
        ]
        if (
            not bot_roles
            or not target_roles
            or not max(bot_roles) > max(target_roles)
        ):
            raise InterviewNotReady("Botのロール順位が対象者より上である必要があります。")
        await self._check_waiting_timeout(record)
        await self._check_age_review(record)
        return member

    async def approve(self, record, actor_id):
        # 呼び出し元で管理者権限を確認し、Welcome共通ロックを取得する。
        if record["status"] not in ("pending", "approved"):
            raise KickRequestConflict("この確認依頼は実行できる状態ではありません。")

        try:
            await self.validate_target(record)
        except KickTargetChanged:
            await self._repository.transition(
                record["request_id"],
                expected=record["status"],
                target="obsolete",
            )
            raise

        if record["status"] == "pending":
            record = await self._repository.decide(
                record["request_id"], actor_id=actor_id, approve=True
            )

        # 承認保存後にも、参加日時を取得し直す。
        try:
            member = await self.validate_target(record)
        except KickTargetChanged:
            await self._repository.transition(
                record["request_id"], expected="approved", target="obsolete"
            )
            raise

        if "waiting_timeout" in record:
            await self._notify_before_kick(record, member)

            # DM送信中に本人が回答した場合も、古い期限ではキックしない。
            try:
                member = await self.validate_target(record)
            except KickTargetChanged:
                await self._repository.transition(
                    record["request_id"],
                    expected="approved",
                    target="obsolete",
                )
                raise

        record = await self._repository.transition(
            record["request_id"], expected="approved", target="executing"
        )
        # executingを保存してから一度だけ実行する。
        # 通信失敗時も、この処理を自動で再実行しない。
        await member.kick(
            reason=f"Wanbot {record['request_id']}: {record['reason'][:200]}"
        )
        return await self.reconcile(record)

    async def reconcile(self, record):
        """実行中の依頼について退出を確認する。キック自体は再実行しない。"""
        if record["status"] != "executing":
            return record

        member = await self._member(record["user_id"])
        if member is not None:
            if not same_membership(
                record,
                guild_id=self._guild_id,
                user_id=member.id,
                session_id=record["session_id"],
                joined_at=member.joined_at,
            ):
                return await self._repository.transition(
                    record["request_id"],
                    expected="executing",
                    target="obsolete",
                )
            return record

        history = await self._welcome.history(record["user_id"])
        session = next(
            (
                item for item in history
                if item.session_id == record["session_id"]
                and item.channel_id == record["channel_id"]
            ),
            None,
        )
        if session is None:
            raise InterviewNotReady("終了するWelcomeを確認できません。")

        if session.state != "closed":
            if session.state != "archiving":
                session = await self._welcome.begin_archive(
                    session.session_id, reason="kick_approved"
                )
            elif session.closed_reason not in ("kick_approved", "member_left"):
                raise InterviewNotReady("別の終了処理が進行しています。")

            channel_id = await self._archive.archive(session)
            if channel_id is not None:
                await self._welcome.record_archive_channel(
                    session.session_id, channel_id
                )
            await self._welcome.close_session(
                session.session_id, reason=session.closed_reason
            )
        elif session.closed_reason not in ("kick_approved", "member_left"):
            raise InterviewNotReady("このWelcomeは別の理由で終了しています。")

        return await self._repository.transition(
            record["request_id"], expected="executing", target="completed"
        )
