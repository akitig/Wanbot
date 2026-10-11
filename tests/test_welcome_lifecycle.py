import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wanbot.config import parse_config
from wanbot.welcome.channels import WelcomeChannelError
from wanbot.welcome.lifecycle import WelcomeLifecycle
from wanbot.welcome.repository import WelcomeConflict, WelcomeRepository


def make_config(tmp_path):
    return parse_config({
        "DISCORD_TOKEN": "test-token",
        "PROD_GUILD_ID": "101",
        "TEST_GUILD_ID": "100",
        "RUNTIME_DATA_DIR": str(tmp_path / "runtime"),
    })


def make_case(tmp_path):
    config = make_config(tmp_path)
    repository = WelcomeRepository(config, 100)
    member = SimpleNamespace(
        id=300,
        guild=SimpleNamespace(id=100),
        bot=False,
    )

    async def ensure(target):
        return await repository.begin(target.id)

    channels = SimpleNamespace(ensure=AsyncMock(side_effect=ensure))
    archive = SimpleNamespace(archive=AsyncMock(return_value=500))
    service = WelcomeLifecycle(
        guild_id=100,
        repository=repository,
        channels=channels,
        archive=archive,
    )
    return SimpleNamespace(
        config=config,
        repository=repository,
        member=member,
        channels=channels,
        archive=archive,
        service=service,
    )


def test_leave_archives_and_closes_session(tmp_path):
    async def run():
        item = make_case(tmp_path)
        try:
            session = await item.repository.begin(300)
            await item.repository.attach_channel(session.session_id, 500)

            closed = await item.service.member_left(item.member)

            assert closed.state == "closed"
            assert closed.closed_reason == "member_left"
            assert closed.channel_id == 500
            assert await item.repository.get_active(300) is None

            submitted = item.archive.archive.call_args.args[0]
            assert submitted.state == "archiving"
        finally:
            await item.repository.close()

    asyncio.run(run())


def test_archive_failure_keeps_pending_and_blocks_new_creation(tmp_path):
    async def run():
        item = make_case(tmp_path)
        try:
            await item.repository.begin(300)
            item.archive.archive.side_effect = RuntimeError("move failed")

            with pytest.raises(RuntimeError):
                await item.service.member_left(item.member)

            pending = await item.repository.get_active(300)
            assert pending.state == "archiving"

            with pytest.raises(RuntimeError):
                await item.service.ensure(item.member)

            item.channels.ensure.assert_not_awaited()
            assert len(await item.repository.history(300)) == 1
        finally:
            await item.repository.close()

    asyncio.run(run())


def test_rejoin_finishes_pending_archive_before_new_session(tmp_path):
    async def run():
        item = make_case(tmp_path)
        try:
            old = await item.repository.begin(300)
            await item.repository.begin_archive(
                old.session_id, reason="member_left"
            )

            new = await item.service.ensure(item.member)

            assert new.session_id != old.session_id
            history = await item.repository.history(300)
            previous = next(
                entry for entry in history
                if entry.session_id == old.session_id
            )
            assert previous.state == "closed"
            assert previous.channel_id == 500
            assert await item.repository.get_active(300) == new
        finally:
            await item.repository.close()

    asyncio.run(run())


def test_restart_recovers_after_final_save_failure(tmp_path):
    async def run():
        item = make_case(tmp_path)
        try:
            session = await item.repository.begin(300)
            item.repository.close_session = AsyncMock(
                side_effect=RuntimeError("save failed")
            )

            with pytest.raises(RuntimeError):
                await item.service.member_left(item.member)

            pending = await item.repository.get_active(300)
            assert pending.state == "archiving"
            assert pending.channel_id == 500
        finally:
            await item.repository.close()

        reopened = WelcomeRepository(item.config, 100)
        try:
            service = WelcomeLifecycle(
                guild_id=100,
                repository=reopened,
                channels=item.channels,
                archive=item.archive,
            )

            assert await service.resume_pending() == (1, 0)
            assert await reopened.pending_archives() == ()

            history = await reopened.history(300)
            assert history[0].session_id == session.session_id
            assert history[0].state == "closed"
            assert history[0].channel_id == 500
        finally:
            await reopened.close()

    asyncio.run(run())


def test_retry_continues_after_one_failure(tmp_path):
    async def run():
        item = make_case(tmp_path)
        try:
            first = await item.repository.begin(300)
            second = await item.repository.begin(301)

            for session in (first, second):
                await item.repository.begin_archive(
                    session.session_id, reason="member_left"
                )

            async def archive(session):
                if session.user_id == 300:
                    raise RuntimeError("move failed")
                return 501

            item.archive.archive.side_effect = archive

            assert await item.service.resume_pending() == (1, 1)
            assert (await item.repository.get_active(300)).state == "archiving"
            assert await item.repository.get_active(301) is None
        finally:
            await item.repository.close()

    asyncio.run(run())


def test_leave_without_session_does_nothing(tmp_path):
    async def run():
        item = make_case(tmp_path)
        try:
            assert await item.service.member_left(item.member) is None
            item.archive.archive.assert_not_awaited()
        finally:
            await item.repository.close()

    asyncio.run(run())


def test_duplicate_leave_does_not_archive_twice(tmp_path):
    async def run():
        item = make_case(tmp_path)
        try:
            await item.repository.begin(300)

            await item.service.member_left(item.member)
            await item.service.member_left(item.member)

            item.archive.archive.assert_awaited_once()
        finally:
            await item.repository.close()

    asyncio.run(run())


def test_leave_before_channel_creation_can_finish(tmp_path):
    async def run():
        item = make_case(tmp_path)
        try:
            await item.repository.begin(300)
            item.archive.archive.return_value = None

            closed = await item.service.member_left(item.member)

            assert closed.state == "closed"
            assert closed.channel_id is None
        finally:
            await item.repository.close()

    asyncio.run(run())


@pytest.mark.parametrize("case", ["bot", "other_guild"])
def test_invalid_member_is_rejected(tmp_path, case):
    async def run():
        item = make_case(tmp_path)
        try:
            if case == "bot":
                item.member.bot = True
            else:
                item.member.guild.id = 999

            with pytest.raises(WelcomeChannelError):
                await item.service.member_left(item.member)
            with pytest.raises(WelcomeChannelError):
                await item.service.ensure(item.member)

            item.archive.archive.assert_not_awaited()
            item.channels.ensure.assert_not_awaited()
        finally:
            await item.repository.close()

    asyncio.run(run())


def test_recovered_channel_cannot_replace_existing_channel(tmp_path):
    async def run():
        item = make_case(tmp_path)
        try:
            session = await item.repository.begin(300)
            await item.repository.attach_channel(session.session_id, 500)
            await item.repository.begin_archive(
                session.session_id, reason="member_left"
            )

            with pytest.raises(WelcomeConflict):
                await item.repository.record_archive_channel(
                    session.session_id, 501
                )

            assert (await item.repository.get_active(300)).channel_id == 500
        finally:
            await item.repository.close()

    asyncio.run(run())


def test_recovered_channel_cannot_belong_to_another_session(tmp_path):
    async def run():
        item = make_case(tmp_path)
        try:
            first = await item.repository.begin(300)
            await item.repository.attach_channel(first.session_id, 500)
            second = await item.repository.begin(301)
            await item.repository.begin_archive(
                second.session_id, reason="member_left"
            )

            with pytest.raises(WelcomeConflict):
                await item.repository.record_archive_channel(
                    second.session_id, 500
                )

            assert (await item.repository.get_active(301)).channel_id is None
        finally:
            await item.repository.close()

    asyncio.run(run())
