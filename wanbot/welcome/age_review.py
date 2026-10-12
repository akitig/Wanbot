"""25歳未満の再確認を、既存の管理者キック承認へ接続する。"""

from types import SimpleNamespace

from wanbot.welcome.interview_gate import InterviewNotReady
from wanbot.welcome.kick_execution import KickTargetChanged


REASON = (
    "本人が「25歳未満」と回答し、再確認でも25歳未満であることを確認しました。"
    "サーバーの参加条件は25歳以上です。"
)


class AgeReview:
    def __init__(
        self, *, client, guild_id, welcome, questions,
        admissions, review, is_available,
    ):
        self._client = client
        self._guild_id = guild_id
        self._welcome = welcome
        self._questions = questions
        self._admissions = admissions
        self._review = review
        self._available = is_available

    async def _confirmed(self, session_id, user_id):
        snapshot = await self._questions.get(session_id)
        if snapshot is None:
            raise InterviewNotReady("年齢回答の保存データを確認できません。")
        if (
            snapshot.session_id != session_id
            or snapshot.user_id != user_id
        ):
            raise InterviewNotReady("年齢回答の対象が一致しません。")
        progress = snapshot.progress
        return (
            progress.status == "age_review_required"
            and progress.adult is False
            and progress.underage_confirmed is True
        )

    async def validate_locked(self, record):
        """キック承認時に呼ぶ。呼出元がWelcome共通ロックを取得する。"""
        if not self._available():
            raise InterviewNotReady("現在、年齢回答を再確認できません。")
        if (
            record.get("age_review") is not True
            or record["guild_id"] != self._guild_id
        ):
            raise InterviewNotReady("年齢確認依頼の対象が不正です。")

        session = await self._welcome.get_active(record["user_id"])
        if (
            session is None
            or session.state != "active"
            or session.session_id != record["session_id"]
            or session.user_id != record["user_id"]
            or session.channel_id != record["channel_id"]
        ):
            raise KickTargetChanged("現在のWelcomeが変わっています。")
        if await self._admissions.get(record["session_id"]) is not None:
            raise KickTargetChanged("すでに参加承認されています。")
        if not await self._confirmed(record["session_id"], record["user_id"]):
            raise KickTargetChanged(
                "現在の回答では、25歳未満の再確認が成立していません。"
            )
        return True

    async def refresh_session_locked(self, session):
        """定期処理から呼ぶ。呼出元がWelcome共通ロックを取得する。"""
        if not self._available() or session.state != "active":
            return
        current = await self._welcome.get_active(session.user_id)
        if (
            current is None
            or current.state != "active"
            or current.session_id != session.session_id
            or current.user_id != session.user_id
            or current.channel_id != session.channel_id
        ):
            return
        if await self._admissions.get(session.session_id) is not None:
            return

        snapshot = await self._questions.get(session.session_id)
        if snapshot is None:
            return
        if not await self._confirmed(session.session_id, session.user_id):
            return

        records = await self._review._repository.for_session(session.session_id)
        for record in records:
            if (
                record["guild_id"] != self._guild_id
                or record["user_id"] != session.user_id
                or record["channel_id"] != session.channel_id
            ):
                raise InterviewNotReady("キック確認依頼の対象が一致しません。")

        age_records = [
            item for item in records if item.get("age_review") is True
        ]
        if len(age_records) > 1:
            raise InterviewNotReady("年齢確認依頼が複数あります。")
        if age_records:
            record = age_records[0]
            # キャンセル済み・無効・完了済みの依頼は作り直さない。
            if record["status"] in ("pending", "approved"):
                key = (record["request_id"], record["status"])
                if key not in self._review._posted:
                    channel, config = await self._review._channel()
                    await self.validate_locked(record)
                    await self._review._publish(record, channel, config)
            return

        if any(
            item["status"] in ("pending", "approved", "executing", "completed")
            for item in records
        ):
            return

        # 既存の管理者専用チャンネル検証を利用する。
        channel, config = await self._review._channel()
        member = await channel.guild.fetch_member(session.user_id)
        if (
            member.id != session.user_id
            or member.guild.id != self._guild_id
            or member.bot
            or member.joined_at is None
            or self._client.user is None
        ):
            raise InterviewNotReady("今回のサーバー参加を確認できません。")

        context = SimpleNamespace(
            guild_id=self._guild_id,
            session_id=session.session_id,
            user_id=session.user_id,
            channel_id=session.channel_id,
        )
        # 通信待ちの後にも、回答とWelcomeを確認する。
        await self.validate_locked({
            "age_review": True,
            "guild_id": self._guild_id,
            "session_id": session.session_id,
            "user_id": session.user_id,
            "channel_id": session.channel_id,
        })
        record = await self._review._repository.create(
            context=context,
            joined_at=member.joined_at,
            actor_id=self._client.user.id,
            reason=REASON,
            age_review=True,
        )
        await self._review._publish(record, channel, config)
