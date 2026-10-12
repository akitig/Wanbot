"""Botが受信した個人DMを、設定されたユーザーへ転送する。"""

import asyncio
import logging
from collections import OrderedDict

import discord

from wanbot.logging_config import log_event


MAX_FILE_BYTES = 8 * 1024 * 1024
RECENT_MESSAGES = 1024


def split_text(text, limit=1900):
    """絵文字も考慮して、送信可能な長さへ分割する。"""
    parts = []
    current = []
    size = 0
    for character in text:
        width = 2 if ord(character) > 0xFFFF else 1
        if size + width > limit:
            parts.append("".join(current))
            current = []
            size = 0
        current.append(character)
        size += width
    if current:
        parts.append("".join(current))
    return parts


def safe_name(value):
    value = " ".join(value.split())[:160]
    return discord.utils.escape_markdown(
        discord.utils.escape_mentions(value)
    )


class DMForwarder:
    def __init__(self, client, config, *, is_available):
        self._client = client
        self._config = config
        self._is_available = is_available
        self._lock = asyncio.Lock()
        self._completed = OrderedDict()

    async def _send(self, destination, content, **kwargs):
        if not self._is_available():
            raise RuntimeError("DM forwarding is unavailable")
        await destination.send(
            content,
            allowed_mentions=discord.AllowedMentions.none(),
            suppress_embeds=True,
            **kwargs,
        )

    async def _attachment(self, destination, attachment, message_id):
        file = None
        if attachment.size <= MAX_FILE_BYTES:
            try:
                file = await attachment.to_file(
                    use_cached=True,
                    spoiler=attachment.is_spoiler(),
                )
            except discord.HTTPException:
                # 本文と、ほかの添付ファイルの転送は続ける。
                file = None

        label = safe_name(attachment.filename)
        if file is None:
            text = (
                f"添付：{label}\n"
                f"元メッセージID：{message_id}\n"
                "容量超過または取得不可のため、リンクで案内します。\n"
                f"{attachment.url}"
            )
            for part in split_text(text):
                await self._send(destination, part)
            return

        try:
            await self._send(
                destination,
                f"添付：{label}\n元メッセージID：{message_id}",
                file=file,
            )
        finally:
            file.close()

    async def handle(self, message):
        if (
            not self._config.dm_forward_enabled
            or not self._is_available()
            or message.guild is not None
            or message.channel.type != discord.ChannelType.private
            or message.author.bot
            or message.webhook_id is not None
        ):
            return False

        async with self._lock:
            if not self._is_available():
                return False
            if message.id in self._completed:
                return False

            try:
                destination_id = self._config.dm_forward_user_id
                if (
                    type(destination_id) is not int
                    or not 0 < destination_id < 2**64
                    or destination_id == getattr(self._client.user, "id", None)
                ):
                    raise ValueError("Invalid DM destination")

                destination = await self._client.fetch_user(destination_id)
                if destination.id != destination_id or destination.bot:
                    raise ValueError("Invalid DM destination")

                author = message.author
                header = (
                    "【受信DM】\n"
                    f"送信者：{safe_name(author.display_name)}"
                    f"（{safe_name(author.name)}）\n"
                    f"送信者ID：{author.id}\n"
                    f"日時：<t:{int(message.created_at.timestamp())}:F>\n"
                    f"元メッセージID：{message.id}"
                )
                await self._send(destination, header)

                body = message.content or "（本文なし）"
                for part in split_text(body):
                    await self._send(destination, part)

                for attachment in message.attachments:
                    await self._attachment(
                        destination, attachment, message.id
                    )

                self._completed[message.id] = None
                if len(self._completed) > RECENT_MESSAGES:
                    self._completed.popitem(last=False)

                log_event("dm_forward", "forward_completed")
                return True
            except Exception as error:
                # DM本文・名前・添付URL・例外本文を一般ログへ出さない。
                log_event(
                    "dm_forward",
                    "forward_failed",
                    level=logging.ERROR,
                    error=error,
                )
                return False
