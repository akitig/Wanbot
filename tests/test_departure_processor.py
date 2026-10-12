import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from wanbot.leave_log.audit_match import AuditEvidence, AuditMatch
from wanbot.leave_log.audit_reader import AuditLookup
from wanbot.leave_log.models import DepartureSnapshot, encode_snapshot
from wanbot.leave_log.processor import DepartureProcessor


NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def setup_processor(seconds=45):
    snapshot = DepartureSnapshot(
        event_id="a" * 32, guild_id=100, user_id=300,
        username="test_user", display_name=None, roles=None,
        joined_at=None, observed_at=NOW,
    )
    record = {
        "snapshot": encode_snapshot(snapshot),
        "channel_id": None,
        "message_id": None,
    }
    evidence = AuditEvidence(
        entry_id=1000, guild_id=100, target_id=300,
        action="kick", created_at=NOW,
    )
    repository = SimpleNamespace(
        pending=AsyncMock(return_value=[record]),
    )
    results = SimpleNamespace(
        get=AsyncMock(return_value=None),
        used_entry_ids=AsyncMock(return_value=frozenset()),
        save=AsyncMock(),
    )

    async def save(saved_snapshot, result):
        results.get.return_value = (saved_snapshot, result)

    results.save.side_effect = save
    delivery = SimpleNamespace(send=AsyncMock(return_value=600))
    reader = AsyncMock(
        return_value=AuditLookup((evidence,), True, "fetched")
    )
    current = [NOW + timedelta(seconds=seconds)]
    available = Mock(return_value=True)
    processor = DepartureProcessor(
        guild_id=100,
        repository=repository,
        results=results,
        delivery=delivery,
        get_guild=lambda: SimpleNamespace(id=100),
        is_available=available,
        clock=lambda: current[0],
        reader=reader,
    )
    return SimpleNamespace(
        processor=processor, snapshot=snapshot, record=record,
        evidence=evidence, repository=repository, results=results,
        delivery=delivery, reader=reader, current=current,
        available=available,
    )


def test_does_not_read_before_45_seconds():
    async def run():
        item = setup_processor(44)
        assert await item.processor.tick() == 0
        item.reader.assert_not_awaited()
        item.delivery.send.assert_not_awaited()

    asyncio.run(run())


def test_matched_result_is_saved_before_delivery():
    async def run():
        item = setup_processor()

        async def send(event_id):
            item.results.save.assert_awaited_once()
            assert item.results.get.return_value[1].kind == "kick"
            return 600

        item.delivery.send.side_effect = send
        assert await item.processor.tick() == 1

    asyncio.run(run())


def test_no_match_waits_then_finalizes_unknown():
    async def run():
        item = setup_processor(45)
        item.reader.return_value = AuditLookup((), True, "fetched")
        assert await item.processor.tick() == 0
        item.results.save.assert_not_awaited()

        item.current[0] = NOW + timedelta(seconds=120)
        assert await item.processor.tick() == 1
        result = item.results.save.await_args.args[1]
        assert result.kind == "unknown"
        assert result.explanation == "no_matching_audit"

    asyncio.run(run())


def test_failed_lookup_is_not_classified_as_voluntary():
    async def run():
        item = setup_processor(120)
        item.reader.return_value = AuditLookup((), False, "forbidden")
        assert await item.processor.tick() == 1
        result = item.results.save.await_args.args[1]
        assert result.kind == "unknown"
        assert result.explanation == "audit_unavailable_or_incomplete"

    asyncio.run(run())


def test_delivery_retry_uses_saved_result():
    async def run():
        item = setup_processor()
        item.delivery.send.side_effect = [RuntimeError("send failed"), 600]

        assert await item.processor.tick() == 0
        assert await item.processor.tick() == 1
        assert item.reader.await_count == 1
        assert item.results.save.await_count == 1
        assert item.delivery.send.await_count == 2

    asyncio.run(run())


def test_saved_result_can_be_delivered_immediately():
    async def run():
        item = setup_processor(0)
        item.results.get.return_value = (
            item.snapshot,
            AuditMatch("kick", item.evidence, "matched"),
        )
        assert await item.processor.tick() == 1
        item.reader.assert_not_awaited()

    asyncio.run(run())


def test_used_audit_is_not_reused():
    async def run():
        item = setup_processor(120)
        item.results.used_entry_ids.return_value = frozenset({1000})
        assert await item.processor.tick() == 1
        assert item.results.save.await_args.args[1].kind == "unknown"

    asyncio.run(run())


def test_one_bad_record_does_not_block_another():
    async def run():
        item = setup_processor()
        item.repository.pending.return_value = [
            {"snapshot": {}},
            item.record,
        ]
        assert await item.processor.tick() == 1
        item.delivery.send.assert_awaited_once()

    asyncio.run(run())


def test_unavailable_feature_does_not_process():
    async def run():
        item = setup_processor()
        item.available.return_value = False
        assert await item.processor.tick() == 0
        item.repository.pending.assert_not_awaited()

    asyncio.run(run())
