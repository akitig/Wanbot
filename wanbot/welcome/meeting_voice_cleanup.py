"""終了済みの面談VCを、無人状態が5分続いた後に削除する。"""

import asyncio
import logging
import time
from datetime import datetime, timezone

import discord

from wanbot.logging_config import log_event
from wanbot.storage.async_store import AsyncGuildJsonStore
from wanbot.welcome.interview_gate import InterviewNotReady


class MeetingVoiceCleanup:
    def __init__(
        self, *, client, config, guild_id, voices, welcome_repository,
        is_available, store=None, clock=None,
    ):
        self._client = client
        self._guild_id = guild_id
        self._voices = voices
        self._welcome = welcome_repository
        self._available = is_available
        self._clock = clock or time.monotonic
        self._store = (
            store if store is not None else
            AsyncGuildJsonStore(config, guild_id, "welcome_vc_cleanup")
        )
        self._empty_since = {}
        self._schedule_changes = None
        self._generation = 0
        self._lock = asyncio.Lock()

    def set_schedule_changes(self, repository):
        self._schedule_changes = repository

    def reset(self):
        self._generation += 1
        self._empty_since.clear()

    def voice_changed(self, before, after):
        before_id = getattr(before.channel, "id", None)
        after_id = getattr(after.channel, "id", None)
        if before_id == after_id:
            return
        self._generation += 1
        for channel_id in (before_id, after_id):
            self._empty_since.pop(channel_id, None)

    def _deleted(self, data):
        if data == {}:
            return {}
        if (
            type(data) is not dict
            or set(data) != {"version", "deleted"}
            or type(data["version"]) is not int
            or data["version"] != 1
            or type(data["deleted"]) is not dict
        ):
            raise InterviewNotReady("面談VCの削除記録が不正です。")
        for key, value in data["deleted"].items():
            if (
                not isinstance(key, str)
                or not key.isdigit()
                or type(value) is not dict
                or set(value) != {"proposal_id", "recorded_at"}
                or not isinstance(value["proposal_id"], str)
                or not isinstance(value["recorded_at"], str)
            ):
                raise InterviewNotReady("面談VCの削除記録が不正です。")
        return data["deleted"]

    async def _mark_deleted(self, record):
        def change(data):
            entries = self._deleted(data)
            entries[str(record["channel_id"])] = {
                "proposal_id": record["proposal_id"],
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }
            return {"version": 1, "deleted": entries}

        await self._store.update(change)
        self._empty_since.pop(record["channel_id"], None)

    async def _finished(self, record):
        identity = record["identity"]
        user_id = identity["participants"][0]
        history = await self._welcome.history(user_id)
        if any(
            session.session_id == identity["session_id"]
            and session.channel_id == identity["welcome_channel_id"]
            and session.state == "closed"
            for session in history
        ):
            return True

        if self._schedule_changes is None:
            return False

        changes = await self._schedule_changes.for_session(
            identity["session_id"]
        )
        for change in changes:
            if change["proposal_id"] != record["proposal_id"]:
                continue
            if (
                change["guild_id"] != self._guild_id
                or change["session_id"] != identity["session_id"]
                or change["user_id"] != user_id
                or change["channel_id"] != identity["welcome_channel_id"]
            ):
                raise InterviewNotReady(
                    "日程変更と削除対象VCの所属が一致しません。"
                )
            if change["status"] == "completed":
                return True
        return False

    def _check_channel(self, channel, record):
        if (
            not isinstance(channel, discord.VoiceChannel)
            or channel.id != record["channel_id"]
            or channel.guild.id != self._guild_id
            or channel.category_id != record["identity"]["category_id"]
        ):
            raise InterviewNotReady("削除対象の面談VCが保存記録と一致しません。")

    async def _fetch(self, record):
        try:
            return await self._client.fetch_channel(record["channel_id"])
        except discord.NotFound as error:
            if error.code != 10003:
                raise
            await self._mark_deleted(record)
            return None

    async def _check_one(self, record):
        channel_id = record["channel_id"]
        if not await self._finished(record):
            self._empty_since.pop(channel_id, None)
            return
        if not self._available():
            self.reset()
            return

        generation = self._generation
        channel = await self._fetch(record)
        if channel is None:
            return
        self._check_channel(channel, record)

        if generation != self._generation or not self._available():
            return
        # membersではなく、接続中のユーザーIDの一覧で確認する。
        if channel.voice_states:
            self._empty_since.pop(channel_id, None)
            return

        now = self._clock()
        started = self._empty_since.setdefault(channel_id, now)
        if now - started < 300:
            return

        # 通信待ちの間の入退室や、接続切断も確認する。
        if not await self._finished(record):
            self._empty_since.pop(channel_id, None)
            return
        channel = await self._fetch(record)
        if channel is None:
            return
        self._check_channel(channel, record)
        if (
            generation != self._generation
            or not self._available()
            or self._empty_since.get(channel_id) != started
        ):
            return
        if channel.voice_states:
            self._empty_since.pop(channel_id, None)
            return

        try:
            await channel.delete(
                reason="Wanbot completed meeting: empty for 5 minutes"
            )
        except discord.NotFound as error:
            if error.code != 10003:
                raise

        await self._mark_deleted(record)
        log_event(
            "welcome",
            "meeting_vc_deleted",
            guild_id=self._guild_id,
        )

    async def tick(self):
        async with self._lock:
            if not self._available():
                self.reset()
                return
            deleted = self._deleted(await self._store.read())
            for record in await self._voices.cleanup_records():
                if str(record["channel_id"]) in deleted:
                    continue
                try:
                    await self._check_one(record)
                except Exception as error:
                    # 状態を確認できない時間を、無人時間に加算しない。
                    self._empty_since.pop(record["channel_id"], None)
                    log_event(
                        "welcome",
                        "meeting_vc_cleanup_failed",
                        guild_id=self._guild_id,
                        level=logging.ERROR,
                        error=error,
                    )

    async def close(self):
        self.reset()
        await self._store.close()
