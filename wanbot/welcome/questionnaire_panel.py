"""Welcomeの質問開始パネルを投稿・復旧する。"""

import asyncio

import discord

from wanbot.welcome.channels import WelcomeChannelError


PANEL_TEXT = (
    "サーバーへようこそ！\n\n"
    "まずは、参加にあたっての質問にご回答ください。\n"
    "下のボタンから回答を始められます。"
    "途中の場合は、保存済みの続きから再開できます。"
)


def is_questionnaire_panel(message, *, bot_id, channel_id, custom_id):
    if (
        message.author.id != bot_id
        or message.channel.id != channel_id
        or message.webhook_id is not None
    ):
        return False

    return any(
        getattr(component, "custom_id", None) == custom_id
        for row in message.components
        for component in getattr(row, "children", ())
    )


class QuestionnairePanel:
    def __init__(self, *, client, context, repository, view):
        self._client = client
        self._context = context
        self._repository = repository
        self._view = view
        self._lock = asyncio.Lock()
        self._closed = False
        self._custom_id = (
            f"wanbot:welcome:questions:{context.session_id}"
        )

    async def _channel(self):
        channel = await self._client.fetch_channel(
            self._context.channel_id
        )
        if (
            not isinstance(channel, discord.TextChannel)
            or channel.guild.id != self._context.guild_id
            or channel.id != self._context.channel_id
            or channel.topic != (
                f"wanbot:welcome:{self._context.guild_id}:"
                f"{self._context.session_id}"
            )
            or self._client.user is None
        ):
            raise WelcomeChannelError(
                "Invalid Welcome questionnaire channel"
            )
        return channel

    def _matches(self, message):
        return is_questionnaire_panel(
            message,
            bot_id=self._client.user.id,
            channel_id=self._context.channel_id,
            custom_id=self._custom_id,
        )

    async def ensure(self):
        async with self._lock:
            if self._closed:
                raise WelcomeChannelError("Questionnaire panel is closed")

            channel = await self._channel()
            message_id = await self._repository.get(
                self._context.session_id
            )
            message = None

            if message_id is not None:
                try:
                    message = await channel.fetch_message(message_id)
                except discord.NotFound as error:
                    if error.code != 10008:
                        raise

                if message is not None and not self._matches(message):
                    raise WelcomeChannelError(
                        "Saved questionnaire panel does not match"
                    )

            if message is None:
                # 投稿成功後、ID保存前に停止した場合の復旧。
                # 件数を限定せず、対象パネルが見つかるまで探す。
                async for candidate in channel.history(limit=None):
                    if self._matches(candidate):
                        message = candidate
                        break

            if message is None:
                message = await channel.send(
                    PANEL_TEXT,
                    view=self._view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            else:
                # 再起動後のボタンを、新しいViewへ接続する。
                await message.edit(
                    content=PANEL_TEXT,
                    view=self._view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )

            await self._repository.save(
                self._context.session_id,
                message.id,
            )
            return message.id

    def close(self):
        self._closed = True
        self._view.stop()
