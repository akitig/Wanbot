"""対応待ちを再確認して、管理者への催促を送信する。"""

from datetime import datetime

import discord

from wanbot.welcome.staff_reminder_target import StaffReminderTargetError


MESSAGES = {
    "date_selection": (
        "面談日程の選択待ちです。"
        "このWelcomeの面談日程調整から、対応をお願いします。"
    ),
    "second_manager": (
        "2人目の管理者の参加可否が未回答です。"
        "指名された管理者は、面談の参加確認へ回答をお願いします。"
    ),
    "kick_review": (
        "キック確認依頼の対応待ちです。"
        "このチャンネルの確認依頼から、承認またはキャンセルをお願いします。"
    ),
}


class StaffReminderDelivery:
    def __init__(
        self, *, repository, guild_id, kick_channel_id,
        lock, is_available, clock, load_target, resolve_channel,
    ):
        self._repository = repository
        self._guild_id = guild_id
        self._kick_channel_id = kick_channel_id
        self._lock = lock
        self._is_available = is_available
        self._clock = clock
        self._load_target = load_target
        self._resolve_channel = resolve_channel

    async def refresh(self):
        if not self._is_available():
            return

        async with self._lock:
            if not self._is_available():
                return

            target = await self._load_target()
            if target is None:
                return
            if target.kind not in MESSAGES:
                raise StaffReminderTargetError("催促の種類が不明です。")

            channel_id = (
                self._kick_channel_id
                if target.kind == "kick_review"
                else target.welcome_channel_id
            )
            record = await self._repository.ensure(
                target.key,
                session_id=target.session_id,
                channel_id=channel_id,
                now=self._clock(),
            )
            if (
                not record["active"]
                or self._clock() < datetime.fromisoformat(record["next_at"])
            ):
                return

            # resolve_channelは、投稿先の権限も検証してから返す。
            channel, role_ids = await self._resolve_channel(target)
            role_ids = tuple(sorted(set(role_ids)))
            if (
                not isinstance(channel, discord.TextChannel)
                or channel.guild.id != self._guild_id
                or channel.id != channel_id
                or not role_ids
                or any(
                    type(value) is not int or not 0 < value < 2**64
                    for value in role_ids
                )
            ):
                raise StaffReminderTargetError("催促先の設定が不正です。")

            # チャンネル取得中に対応が完了していないか、再確認する。
            if not self._is_available():
                return
            if await self._load_target() != target:
                return

            attempt_id = await self._repository.claim(
                target.key, now=self._clock()
            )
            if attempt_id is None:
                return

            embed = discord.Embed(
                title="管理者の対応をお願いします",
                description=(
                    MESSAGES[target.kind]
                    + "\n\n対象のWelcome："
                    + f"https://discord.com/channels/{self._guild_id}/"
                    + str(target.welcome_channel_id)
                ),
            )
            await channel.send(
                content=" ".join(f"<@&{value}>" for value in role_ids),
                embed=embed,
                allowed_mentions=discord.AllowedMentions(
                    everyone=False,
                    users=False,
                    roles=[discord.Object(id=value) for value in role_ids],
                    replied_user=False,
                ),
            )
            await self._repository.mark_sent(
                target.key,
                attempt_id=attempt_id,
                now=self._clock(),
            )
