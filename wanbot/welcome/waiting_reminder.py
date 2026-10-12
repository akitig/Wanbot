"""本人待ち48時間のリマインドをWelcomeへ投稿する。"""

from datetime import datetime, timedelta

import discord

from wanbot.welcome.waiting_clock_repository import WaitingClockConflict
from wanbot.welcome.waiting_limits import DEFAULT_LIMITS, duration_text


GUIDANCE = {
    "introduction": "自己紹介の入力・年齢確認を完了してください。",
    "rules": "ルールを確認して、指定のリアクションを付けてください。",
    "diagnosis": "VALORANT診断を完了してください。",
    "interview_preferences": "面談希望日時を入力し、送信してください。",
}


class WaitingReminder:
    def __init__(self, *, monitor):
        self._monitor = monitor
        self._controller = monitor._controller
        self._repository = monitor._repository

    async def _channel(self):
        controller = self._controller
        context = controller._context
        await controller._session()
        channel = await controller._client.fetch_channel(context.channel_id)
        if (
            not isinstance(channel, discord.TextChannel)
            or channel.id != context.channel_id
            or channel.guild.id != context.guild_id
            or channel.topic != (
                f"wanbot:welcome:{context.guild_id}:{context.session_id}"
            )
            or controller._client.user is None
        ):
            raise WaitingClockConflict("Welcomeチャンネルを確認できません。")
        return channel

    async def refresh(self):
        controller = self._controller
        if not self._monitor.available():
            return

        async with controller._lock:
            record, action = await self._monitor.candidate_locked()
            if action != "remind":
                return

            epoch = record["epoch"]
            context = controller._context
            marker = f"wanbot:welcome:waiting-reminder:{context.session_id}:{epoch}"
            channel = await self._channel()

            existing = None
            async for message in channel.history(limit=None):
                if (
                    message.author.id == controller._client.user.id
                    and message.webhook_id is None
                    and any(
                        embed.footer.text == marker
                        for embed in message.embeds
                    )
                ):
                    existing = message
                    break

            # 履歴取得中に診断が進む場合などもあるため、送信前に再確認する。
            latest, action = await self._monitor.candidate_locked()
            if (
                action != "remind"
                or latest is None
                or latest["epoch"] != epoch
            ):
                return

            await controller._session()
            if not self._monitor.available():
                return

            if existing is None:
                guidance = GUIDANCE.get(latest["reason"])
                if guidance is None:
                    raise WaitingClockConflict("リマインド対象の状態が不正です。")

                limits = getattr(self._repository, "limits", DEFAULT_LIMITS)
                duration = duration_text(limits.reminder_seconds)
                deadline = (
                    datetime.fromisoformat(latest["started_at"])
                    + timedelta(seconds=limits.timeout_seconds)
                )
                timestamp = int(deadline.timestamp())
                embed = discord.Embed(
                    title="参加手続きのリマインド",
                    description=(
                        f"手続きが進まないまま、本人待ちの時間が{duration}に達しました。\n\n"
                        f"{guidance}\n"
                        "参加手続きの一覧から再開できます。\n\n"
                        f"現在の対応期限：<t:{timestamp}:F>\n"
                        "期限までに手続きが進まない場合は、"
                        "管理者へキックの承認を依頼します。\n"
                        "期限になっただけで、自動的にキックすることはありません。"
                    ),
                )
                embed.set_footer(text=marker)
                await channel.send(
                    content=f"<@{context.user_id}>",
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions(
                        everyone=False,
                        roles=False,
                        users=[discord.Object(id=context.user_id)],
                        replied_user=False,
                    ),
                )

            # 投稿済みなら、保存失敗後の再試行でも再送せず記録だけ行う。
            await self._repository.mark_reminded(
                context.session_id,
                expected_epoch=epoch,
                now=controller._clock(),
            )
