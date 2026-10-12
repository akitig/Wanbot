"""退出記録の照合・結果保存・通知を順番に進める。"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from wanbot.leave_log.audit_match import match_audit
from wanbot.leave_log.audit_reader import (
    AuditLookup,
    SETTLE_DELAY,
    read_departure_audit,
)
from wanbot.leave_log.models import decode_snapshot
from wanbot.logging_config import log_event


FINALIZE_AFTER = timedelta(minutes=2)


class DepartureProcessor:
    def __init__(
        self, *, guild_id, repository, results, delivery,
        get_guild, is_available, clock=None, reader=None,
    ):
        self._guild_id = guild_id
        self._repository = repository
        self._results = results
        self._delivery = delivery
        self._get_guild = get_guild
        self._available = is_available
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._reader = reader or read_departure_audit
        self._lock = asyncio.Lock()

    async def _process(self, record):
        snapshot = decode_snapshot(record["snapshot"])
        if snapshot.guild_id != self._guild_id:
            raise ValueError("Departure guild does not match")

        saved = await self._results.get(snapshot.event_id)
        if saved is not None:
            if saved[0] != snapshot:
                raise ValueError("Departure result snapshot does not match")
        else:
            now = self._clock()
            if now < snapshot.observed_at + SETTLE_DELAY:
                return False

            guild = self._get_guild()
            if guild is None:
                lookup = AuditLookup((), False, "guild_unavailable")
            else:
                lookup = await self._reader(guild, snapshot, now=now)

            if not self._available():
                return False

            used = await self._results.used_entry_ids()
            result = match_audit(
                snapshot,
                lookup.entries,
                lookup_complete=lookup.complete,
                used_entry_ids=used,
            )
            if (
                result.kind == "unknown"
                and self._clock() < snapshot.observed_at + FINALIZE_AFTER
            ):
                return False

            await self._results.save(snapshot, result)

        if not self._available():
            return False

        message_id = await self._delivery.send(snapshot.event_id)
        return message_id is not None

    async def tick(self):
        if not self._available():
            return 0
        async with self._lock:
            if not self._available():
                return 0
            completed = 0
            for record in await self._repository.pending():
                if not self._available():
                    break
                try:
                    if await self._process(record):
                        completed += 1
                except Exception as error:
                    # 1件の失敗で、ほかの人の退出記録まで止めない。
                    # 名前・回答・監査ログ理由は一般ログに出さない。
                    log_event(
                        "leave_log",
                        "processing_failed",
                        guild_id=self._guild_id,
                        level=logging.ERROR,
                        error=error,
                    )
            return completed
