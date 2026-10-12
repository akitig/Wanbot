"""チェックリストを投稿・更新し、必要なときだけ末尾へ移す。"""

import logging

import discord

from wanbot.logging_config import log_event
from wanbot.welcome.input_activity import activity_for
from wanbot.welcome.channels import WelcomeChannelError
from wanbot.welcome.questionnaire_panel import QuestionnairePanel


class ChecklistPanel(QuestionnairePanel):
    def __init__(self, *, view_factory, **kwargs):
        super().__init__(**kwargs)
        self._view_factory = view_factory

    async def ensure(self, *, move_to_bottom=True):
        async with self._lock:
            if self._closed:
                raise WelcomeChannelError("Checklist is closed")

            channel = await self._channel()
            message_id = await self._repository.get(self._context.session_id)
            message = None

            if message_id is not None:
                try:
                    message = await channel.fetch_message(message_id)
                except discord.NotFound as error:
                    if error.code != 10008:
                        raise
                if message is not None and not self._matches(message):
                    raise WelcomeChannelError("Saved checklist does not match")

            if message is None:
                async for candidate in channel.history(limit=None):
                    if self._matches(candidate):
                        message = candidate
                        break

            activity = activity_for(self._context)
            if activity is not None and activity.busy:
                move_to_bottom = False

            move = False
            if move_to_bottom and message is not None:
                async for latest in channel.history(limit=1):
                    move = latest.id != message.id
                    break

            new_view = await self._view_factory()
            old_message = message if move else None

            # 同じcustom_idの新しいViewを登録する前に、古いViewを停止する。
            self._view.stop()
            self._view = new_view

            if message is None or move:
                message = await channel.send(
                    view=new_view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            else:
                # 従来の文章付きパネルから新レイアウトへ切り替える。
                await message.edit(
                    content=None,
                    embeds=[],
                    view=new_view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )

            await self._repository.save(
                self._context.session_id, message.id
            )

            if old_message is not None:
                try:
                    await old_message.delete()
                except discord.NotFound:
                    pass
                except discord.HTTPException as error:
                    log_event(
                        "welcome",
                        "old_checklist_delete_failed",
                        guild_id=self._context.guild_id,
                        level=logging.WARNING,
                        error=error,
                    )

            return message.id
