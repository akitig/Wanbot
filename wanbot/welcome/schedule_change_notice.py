"""日程変更後の再入力案内を、対象のWelcomeへ一度だけ投稿する。"""

from datetime import datetime

import discord

from wanbot.welcome.interview_gate import InterviewNotReady


class ScheduleChangeNotice:
    def __init__(self, *, controller, repository):
        self._controller = controller
        self._repository = repository

    async def _candidate(self):
        controller = self._controller
        context = controller._context
        records = await self._repository.for_session(context.session_id)
        if not records:
            return None

        for record in records:
            if (
                record["guild_id"] != context.guild_id
                or record["session_id"] != context.session_id
                or record["user_id"] != context.user_id
                or record["channel_id"] != context.channel_id
            ):
                raise InterviewNotReady("日程変更の通知対象が一致しません。")

        # 古い変更依頼について、まとめて通知を送ることを防ぐ。
        latest = max(
            records,
            key=lambda item: (
                datetime.fromisoformat(item["created_at"]),
                item["change_id"],
            ),
        )
        if latest["status"] != "completed":
            return None

        # 本人がすでに再提出していたら、再入力の通知は不要。
        if await controller.load_request() is not None:
            return None
        if await controller._plan() is not None:
            return None
        return latest

    async def refresh_locked(self):
        """呼出元はWelcome共通ロックを取得する。"""
        controller = self._controller
        context = controller._context
        await controller._session()
        record = await self._candidate()
        if record is None:
            return

        client = controller._client
        channel = await client.fetch_channel(context.channel_id)
        if (
            not isinstance(channel, discord.TextChannel)
            or channel.id != context.channel_id
            or channel.guild.id != context.guild_id
            or channel.topic != (
                f"wanbot:welcome:{context.guild_id}:{context.session_id}"
            )
            or client.user is None
        ):
            raise InterviewNotReady("日程変更の通知先を確認できません。")

        marker = f"wanbot:welcome:schedule-change:{record['change_id']}"
        async for message in channel.history(limit=None):
            if (
                message.author.id == client.user.id
                and message.webhook_id is None
                and any(embed.footer.text == marker for embed in message.embeds)
            ):
                return

        await controller._session()
        if await self._candidate() != record:
            return

        manager_requested = record["source"] == "manager"
        content = (
            f"<@{context.user_id}> 管理者から面談希望日時の再提出依頼です。"
            if manager_requested else
            "面談日程の変更を受け付けました。"
        )
        embed = discord.Embed(
            title="面談希望日時を入力してください",
            description=(
                "参加手続き一覧の「面談の希望日時を入力する」から、"
                "第一希望〜第三希望を入力し、送信してください。\n\n"
                "以前の希望日時・面談予定は取り消されています。"
                "再提出後に、管理者が日程と担当者を調整します。"
            ),
        )
        embed.set_footer(text=marker)
        await channel.send(
            content=content,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(
                everyone=False,
                roles=False,
                users=(
                    [discord.Object(id=context.user_id)]
                    if manager_requested else False
                ),
                replied_user=False,
            ),
        )
