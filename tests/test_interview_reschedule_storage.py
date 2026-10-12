"""再調整時の希望日時の保存と、重複処理の防止。"""

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from wanbot.storage.json_store import StorageError
from wanbot.welcome.interview_repository import (
    InterviewRequestConflict,
    InterviewRequestRepository,
)


SID = "a" * 32
PROPOSAL = "b" * 32
NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)
DATES = (
    "2030-01-02 12:00",
    "2030-01-02 13:00",
    "2030-01-02 14:00",
)
NEW_DATES = (
    "2030-01-03 12:00",
    "2030-01-03 13:00",
    "2030-01-03 14:00",
)


class MemoryStore:
    def __init__(self):
        self.data = {}

    async def read(self):
        return deepcopy(self.data)

    async def update(self, change):
        updated = change(deepcopy(self.data))
        self.data = deepcopy(updated)


def repository(store=None):
    result = InterviewRequestRepository.__new__(
        InterviewRequestRepository
    )
    result._store = store if store is not None else MemoryStore()
    return result


def session():
    return SimpleNamespace(
        session_id=SID,
        user_id=300,
        channel_id=500,
        state="active",
    )


def test_reschedule_preserves_history_and_allows_new_submission():
    async def scenario():
        repo = repository()
        original = await repo.confirm(session(), DATES, now=NOW)

        await repo.archive_for_reschedule(
            original, proposal_id=PROPOSAL
        )

        assert await repo.get(SID) is None
        assert (
            await repo.archived_for_reschedule(PROPOSAL)
            == original
        )

        replacement = await repo.confirm(
            session(), NEW_DATES, now=NOW
        )
        assert await repo.get(SID) == replacement
        assert (
            await repo.archived_for_reschedule(PROPOSAL)
            == original
        )

    asyncio.run(scenario())


def test_repeated_reset_after_reload_does_not_remove_new_submission():
    async def scenario():
        repo = repository()
        original = await repo.confirm(session(), DATES, now=NOW)
        await repo.archive_for_reschedule(
            original, proposal_id=PROPOSAL
        )
        replacement = await repo.confirm(
            session(), NEW_DATES, now=NOW
        )

        reloaded = repository(repo._store)
        await reloaded.archive_for_reschedule(
            original, proposal_id=PROPOSAL
        )

        assert await reloaded.get(SID) == replacement
        assert (
            await reloaded.archived_for_reschedule(PROPOSAL)
            == original
        )

    asyncio.run(scenario())


def test_old_request_cannot_remove_updated_submission():
    async def scenario():
        repo = repository()
        original = await repo.confirm(session(), DATES, now=NOW)
        await repo.archive_for_reschedule(
            original, proposal_id=PROPOSAL
        )
        replacement = await repo.confirm(
            session(), NEW_DATES, now=NOW
        )
        before = await repo._store.read()

        with pytest.raises(InterviewRequestConflict):
            await repo.archive_for_reschedule(
                original, proposal_id="c" * 32
            )

        assert await repo._store.read() == before
        assert await repo.get(SID) == replacement

    asyncio.run(scenario())


def test_same_proposal_cannot_archive_different_request():
    async def scenario():
        repo = repository()
        original = await repo.confirm(session(), DATES, now=NOW)
        await repo.archive_for_reschedule(
            original, proposal_id=PROPOSAL
        )
        replacement = await repo.confirm(
            session(), NEW_DATES, now=NOW
        )
        before = await repo._store.read()

        with pytest.raises(InterviewRequestConflict):
            await repo.archive_for_reschedule(
                replacement, proposal_id=PROPOSAL
            )

        assert await repo._store.read() == before

    asyncio.run(scenario())


def test_existing_storage_without_history_can_be_read():
    async def scenario():
        repo = repository()
        original = await repo.confirm(session(), DATES, now=NOW)
        repo._store.data.pop("reschedule_history")

        assert await repo.get(SID) == original
        assert await repo.archived_for_reschedule(PROPOSAL) is None

        await repo.archive_for_reschedule(
            original, proposal_id=PROPOSAL
        )
        assert await repo.get(SID) is None
        assert (
            await repo.archived_for_reschedule(PROPOSAL)
            == original
        )

    asyncio.run(scenario())


def test_invalid_history_prevents_changes():
    async def scenario():
        repo = repository()
        original = await repo.confirm(session(), DATES, now=NOW)
        repo._store.data["reschedule_history"] = {
            PROPOSAL: {"unexpected": True}
        }
        before = await repo._store.read()

        with pytest.raises(StorageError):
            await repo.archive_for_reschedule(
                original, proposal_id=PROPOSAL
            )

        assert await repo._store.read() == before

    asyncio.run(scenario())
