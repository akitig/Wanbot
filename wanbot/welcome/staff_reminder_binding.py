"""Welcomeの状態と、管理者への催促処理を接続する。"""

import discord

from wanbot.welcome.interview_gate import InterviewNotReady
from wanbot.welcome.no_show_repository import NoShowConflict
from wanbot.welcome.staff_reminder_delivery import StaffReminderDelivery
from wanbot.welcome.staff_reminder_target import (
    StaffReminderTargetError,
    select_staff_target,
)


class StaffReminderBinding:
    def __init__(self, *, controller, repository):
        self._controller = controller
        context = controller._context
        config = controller._meeting_config.guild_for(context.guild_id)
        if config is None or not config.admin_role_ids:
            raise StaffReminderTargetError("管理者ロールを確認できません。")

        self._delivery = StaffReminderDelivery(
            repository=repository,
            guild_id=context.guild_id,
            kick_channel_id=config.ids["KICK_APPROVAL_CHANNEL_ID"],
            lock=controller._lock,
            is_available=self._available,
            clock=controller._clock,
            load_target=self._load_target,
            resolve_channel=self._resolve_channel,
        )

    def _available(self):
        controller = self._controller
        return (
            not controller._closed
            and controller._context.is_available()
        )

    async def _load_target(self):
        # 呼出元がWelcome共通ロックを取得する。
        controller = self._controller
        context = controller._context
        if not self._available():
            return None

        session = await controller._welcome.get_active(
            context.user_id
        )
        if (
            session is None
            or session.session_id != context.session_id
            or session.channel_id != context.channel_id
            or session.state != "active"
        ):
            return None

        if await controller._admissions.get(context.session_id) is not None:
            return None

        reviews = await controller._kick_review._repository.for_session(
            context.session_id
        )
        # キック確認は、面談や不参加の状態より優先する。
        target = select_staff_target(
            context=context,
            request=None,
            plan=None,
            kick_reviews=reviews,
        )
        if target is not None:
            return target
        if any(
            item["status"] in ("executing", "completed")
            for item in reviews
        ):
            return None

        request = await controller.load_request()
        plan = await controller._plan()

        # 不参加への回答待ち、再調整の保存処理中には催促しない。
        binding = getattr(controller, "_no_show", None)
        if binding is not None:
            try:
                await binding.guard_input()
            except (InterviewNotReady, NoShowConflict):
                return None

        return select_staff_target(
            context=context,
            request=request,
            plan=plan,
            kick_reviews=reviews,
        )

    async def _resolve_channel(self, target):
        controller = self._controller
        context = controller._context

        if target.kind == "kick_review":
            # 既存の管理者専用チャンネル検証を必ず通す。
            channel, config = await controller._kick_review._channel()
            return channel, config.admin_role_ids

        config = controller._meeting_config.guild_for(context.guild_id)
        if config is None or not config.admin_role_ids:
            raise StaffReminderTargetError("管理者ロールを確認できません。")

        client = controller._client
        channel = await client.fetch_channel(context.channel_id)
        if (
            not isinstance(channel, discord.TextChannel)
            or channel.guild.id != context.guild_id
            or channel.id != context.channel_id
            or channel.topic != (
                f"wanbot:welcome:{context.guild_id}:{context.session_id}"
            )
            or client.user is None
        ):
            raise StaffReminderTargetError("Welcomeチャンネルが一致しません。")

        bot = await channel.guild.fetch_member(client.user.id)
        permissions = channel.permissions_for(bot)
        if not (
            permissions.view_channel
            and permissions.send_messages
            and permissions.embed_links
        ):
            raise StaffReminderTargetError("催促の送信権限がありません。")

        return channel, config.admin_role_ids

    async def refresh(self):
        await self._delivery.refresh()
