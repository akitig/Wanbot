import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from wanbot.config import parse_config
from wanbot.welcome.channels import WelcomeChannelError
from wanbot.welcome.lifecycle import WelcomeLifecycle
from wanbot.welcome.repository import WelcomeRepository


def api_error(status, code):
    response = SimpleNamespace(status=status, reason="test")
    data = {"code": code, "message": "test"}
    if status == 404:
        return discord.NotFound(response, data)
    if status == 403:
        return discord.Forbidden(response, data)
    return discord.HTTPException(response, data)


def make_case(tmp_path):
    config = parse_config({
        "DISCORD_TOKEN": "test-token",
        "PROD_GUILD_ID": "101",
        "TEST_GUILD_ID": "100",
        "RUNTIME_DATA_DIR": str(tmp_path / "runtime"),
    })
    repository = WelcomeRepository(config, 100)
    guild = SimpleNamespace(
        id=100,
        unavailable=False,
        fetch_member=AsyncMock(),
    )
    guild.fetch_member.return_value = SimpleNamespace(
        id=300,
        guild=guild,
    )
    archive = SimpleNamespace(archive=AsyncMock(return_value=500))
    service = WelcomeLifecycle(
        guild_id=100,
        repository=repository,
        channels=SimpleNamespace(ensure=AsyncMock()),
        archive=archive,
    )
    return SimpleNamespace(
        repository=repository,
        guild=guild,
        archive=archive,
        service=service,
    )


def test_present_member_keeps_session(tmp_path):
    async def run():
        item = make_case(tmp_path)
        try:
            original = await item.repository.begin(300)

            assert await item.service.reconcile_members(item.guild) == (0, 0)

            assert await item.repository.get_active(300) == original
            item.archive.archive.assert_not_awaited()
            item.guild.fetch_member.assert_awaited_once_with(300)
        finally:
            await item.repository.close()

    asyncio.run(run())


def test_unknown_member_is_archived(tmp_path):
    async def run():
        item = make_case(tmp_path)
        try:
            await item.repository.begin(300)
            item.guild.fetch_member.side_effect = api_error(404, 10007)

            assert await item.service.reconcile_members(item.guild) == (1, 0)

            assert await item.repository.get_active(300) is None
            history = await item.repository.history(300)
            assert history[0].state == "closed"
            assert history[0].closed_reason == "member_left"
            assert history[0].channel_id == 500
        finally:
            await item.repository.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "error",
    [
        api_error(403, 50001),
        api_error(404, 10004),
        api_error(500, 0),
        OSError("network unavailable"),
    ],
)
def test_fetch_failure_does_not_mark_member_as_left(tmp_path, error):
    async def run():
        item = make_case(tmp_path)
        try:
            original = await item.repository.begin(300)
            item.guild.fetch_member.side_effect = error

            assert await item.service.reconcile_members(item.guild) == (0, 1)

            assert await item.repository.get_active(300) == original
            assert await item.repository.pending_archives() == ()
            item.archive.archive.assert_not_awaited()
        finally:
            await item.repository.close()

    asyncio.run(run())


def test_retry_succeeds_after_fetch_failure(tmp_path):
    async def run():
        item = make_case(tmp_path)
        try:
            await item.repository.begin(300)
            item.guild.fetch_member.side_effect = [
                OSError("network unavailable"),
                api_error(404, 10007),
            ]

            assert await item.service.reconcile_members(item.guild) == (0, 1)
            assert await item.service.reconcile_members(item.guild) == (1, 0)
            assert await item.repository.get_active(300) is None
        finally:
            await item.repository.close()

    asyncio.run(run())


def test_archive_failure_keeps_pending_for_normal_retry(tmp_path):
    async def run():
        item = make_case(tmp_path)
        try:
            await item.repository.begin(300)
            item.guild.fetch_member.side_effect = api_error(404, 10007)
            item.archive.archive.side_effect = RuntimeError("move failed")

            assert await item.service.reconcile_members(item.guild) == (0, 1)
            assert (await item.repository.get_active(300)).state == "archiving"

            item.archive.archive.side_effect = None

            assert await item.service.resume_pending() == (1, 0)
            assert await item.repository.get_active(300) is None
        finally:
            await item.repository.close()

    asyncio.run(run())


def test_closed_and_archiving_sessions_are_not_fetched(tmp_path):
    async def run():
        item = make_case(tmp_path)
        try:
            first = await item.repository.begin(300)
            await item.repository.close_session(
                first.session_id, reason="member_left"
            )
            second = await item.repository.begin(301)
            await item.repository.begin_archive(
                second.session_id, reason="member_left"
            )

            assert await item.repository.open_sessions() == ()
            assert await item.service.reconcile_members(item.guild) == (0, 0)
            item.guild.fetch_member.assert_not_awaited()
        finally:
            await item.repository.close()

    asyncio.run(run())


def test_wrong_guild_is_rejected(tmp_path):
    async def run():
        item = make_case(tmp_path)
        try:
            original = await item.repository.begin(300)
            item.guild.id = 999

            with pytest.raises(WelcomeChannelError):
                await item.service.reconcile_members(item.guild)

            assert await item.repository.get_active(300) == original
            item.guild.fetch_member.assert_not_awaited()
        finally:
            await item.repository.close()

    asyncio.run(run())
