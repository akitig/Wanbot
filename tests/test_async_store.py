import asyncio
import threading

import pytest

from wanbot.config import parse_config
from wanbot.storage.async_store import AsyncGuildJsonStore
from wanbot.storage.json_store import GuildJsonStore, StorageError


@pytest.fixture
def config(tmp_path):
    return parse_config({
        "DISCORD_TOKEN": "fake-token",
        "TEST_GUILD_ID": "200",
        "RUNTIME_DATA_DIR": str(tmp_path / "runtime"),
    })


def test_async_save_and_read(config):
    async def scenario():
        async with AsyncGuildJsonStore(config, 200, "welcome") as store:
            await store.update(lambda data: {"step": "面談待ち"})
            assert await store.read() == {"step": "面談待ち"}

    asyncio.run(scenario())


def test_concurrent_async_updates_are_not_lost(config):
    async def scenario():
        async with AsyncGuildJsonStore(config, 200, "counter") as store:
            def increment(data):
                data["count"] = data.get("count", 0) + 1
                return data

            await asyncio.gather(
                *(store.update(increment) for _ in range(30))
            )

            assert await store.read() == {"count": 30}

    asyncio.run(scenario())


def test_closed_store_rejects_new_operations(config):
    async def scenario():
        store = AsyncGuildJsonStore(config, 200, "welcome")
        await store.close()
        await store.close()

        with pytest.raises(StorageError):
            await store.read()

        with pytest.raises(StorageError):
            await store.update(lambda data: {})

    asyncio.run(scenario())


def test_storage_error_reaches_caller(config):
    async def scenario():
        async with AsyncGuildJsonStore(config, 200, "welcome") as store:
            with pytest.raises(StorageError):
                await store.update(lambda data: {"bad": float("nan")})

            assert await store.read() == {}

    asyncio.run(scenario())


def test_file_operation_does_not_block_event_loop(config, monkeypatch):
    async def scenario():
        started = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        store = AsyncGuildJsonStore(config, 200, "welcome")

        def slow_read():
            loop.call_soon_threadsafe(started.set)
            if not release.wait(timeout=5):
                raise TimeoutError("test worker was not released")
            return {"finished": True}

        monkeypatch.setattr(store._store, "read", slow_read)
        reading = asyncio.create_task(store.read())

        try:
            await asyncio.wait_for(started.wait(), timeout=2)

            # 別スレッドの読み込みが待機中でも、ここまで進める。
            assert not reading.done()
            release.set()

            assert await reading == {"finished": True}
        finally:
            release.set()
            await store.close()
            await asyncio.gather(reading, return_exceptions=True)

    asyncio.run(scenario())


def test_cancelled_caller_does_not_cancel_started_write(config):
    async def scenario():
        started = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        store = AsyncGuildJsonStore(config, 200, "welcome")
        closing = None

        def slow_update(data):
            loop.call_soon_threadsafe(started.set)
            if not release.wait(timeout=5):
                raise TimeoutError("test worker was not released")
            data["saved"] = True
            return data

        writing = asyncio.create_task(store.update(slow_update))

        try:
            await asyncio.wait_for(started.wait(), timeout=2)

            writing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await writing

            closing = asyncio.create_task(store.close())
            await asyncio.sleep(0)

            # 保存が終わるまでは、終了処理も完了しない。
            assert not closing.done()

            release.set()
            await closing

            reopened = GuildJsonStore(config, 200, "welcome")
            assert reopened.read() == {"saved": True}
        finally:
            release.set()
            await store.close()
            await asyncio.gather(writing, return_exceptions=True)
            if closing is not None:
                await closing

    asyncio.run(scenario())
