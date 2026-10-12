import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from wanbot.leave_log.audit_match import AuditEvidence, AuditMatch
from wanbot.leave_log.models import DepartureSnapshot
from wanbot.leave_log.result_repository import (
    DepartureResultConflict,
    DepartureResultRepository,
)


NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


class MemoryStore:
    def __init__(self):
        self.data = {}

    async def read(self):
        return deepcopy(self.data)

    async def update(self, transform):
        self.data = deepcopy(transform(deepcopy(self.data)))

    async def close(self):
        pass


def snapshot():
    return DepartureSnapshot(
        event_id="a" * 32, guild_id=100, user_id=300,
        username="test_user", display_name=None, roles=None,
        joined_at=None, observed_at=NOW,
    )


def result():
    return AuditMatch(
        "kick",
        AuditEvidence(
            entry_id=1000, guild_id=100, target_id=300,
            action="kick", created_at=NOW,
            actor_id=400, reason="テスト理由",
        ),
        "matched",
    )


def test_result_and_used_entry_survive_restart():
    async def run():
        store = MemoryStore()
        repository = DepartureResultRepository(None, 100, store=store)
        await repository.save(snapshot(), result())

        restored = DepartureResultRepository(None, 100, store=store)
        assert await restored.get(snapshot().event_id) == (snapshot(), result())
        assert await restored.used_entry_ids() == frozenset({1000})

    asyncio.run(run())


def test_same_result_can_be_saved_again():
    async def run():
        repository = DepartureResultRepository(None, 100, store=MemoryStore())
        await repository.save(snapshot(), result())
        await repository.save(snapshot(), result())
        assert await repository.get(snapshot().event_id) == (snapshot(), result())

    asyncio.run(run())


def test_same_audit_cannot_be_used_for_another_departure():
    async def run():
        repository = DepartureResultRepository(None, 100, store=MemoryStore())
        await repository.save(snapshot(), result())
        another = replace(snapshot(), event_id="b" * 32)
        with pytest.raises(DepartureResultConflict):
            await repository.save(another, result())
        assert await repository.get(another.event_id) is None

    asyncio.run(run())


def test_other_target_evidence_is_rejected():
    async def run():
        repository = DepartureResultRepository(None, 100, store=MemoryStore())
        wrong = replace(
            result(), evidence=replace(result().evidence, target_id=999)
        )
        with pytest.raises(DepartureResultConflict):
            await repository.save(snapshot(), wrong)

    asyncio.run(run())


def test_other_guild_is_rejected():
    async def run():
        repository = DepartureResultRepository(None, 999, store=MemoryStore())
        with pytest.raises(DepartureResultConflict):
            await repository.save(snapshot(), result())

    asyncio.run(run())


def test_unknown_result_does_not_consume_audit():
    async def run():
        repository = DepartureResultRepository(None, 100, store=MemoryStore())
        unknown = AuditMatch("unknown", None, "no_matching_audit")
        await repository.save(snapshot(), unknown)
        assert await repository.get(snapshot().event_id) == (snapshot(), unknown)
        assert await repository.used_entry_ids() == frozenset()

    asyncio.run(run())


def test_final_result_cannot_be_silently_overwritten():
    async def run():
        repository = DepartureResultRepository(None, 100, store=MemoryStore())
        await repository.save(snapshot(), result())
        with pytest.raises(DepartureResultConflict):
            await repository.save(
                snapshot(),
                AuditMatch("unknown", None, "no_matching_audit"),
            )
        assert await repository.get(snapshot().event_id) == (snapshot(), result())

    asyncio.run(run())


def test_missing_actor_and_reason_survive_storage():
    async def run():
        repository = DepartureResultRepository(None, 100, store=MemoryStore())
        matched = replace(
            result(), evidence=replace(
                result().evidence, actor_id=None, reason=None
            )
        )
        await repository.save(snapshot(), matched)
        _, restored = await repository.get(snapshot().event_id)
        assert restored.evidence.actor_id is None
        assert restored.evidence.reason is None

    asyncio.run(run())
