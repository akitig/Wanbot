import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from wanbot.config import parse_config
from wanbot.storage import json_store
from wanbot.storage.json_store import GuildJsonStore, StorageError


@pytest.fixture
def config(tmp_path):
    return parse_config({
        "DISCORD_TOKEN": "fake-token",
        "PROD_GUILD_ID": "100",
        "TEST_GUILD_ID": "200",
        "RUNTIME_DATA_DIR": str(tmp_path / "runtime"),
    })


def test_missing_file_returns_empty_without_creating_file(config):
    store = GuildJsonStore(config, 200, "welcome")

    assert store.read() == {}
    assert not store.path.exists()


def test_data_survives_new_store_instance(config):
    store = GuildJsonStore(config, 200, "welcome")
    store.update(lambda data: {"step": "面談希望入力待ち"})

    reopened = GuildJsonStore(config, 200, "welcome")
    assert reopened.read() == {"step": "面談希望入力待ち"}


def test_production_and_test_are_separate(config):
    production = GuildJsonStore(config, 100, "welcome")
    testing = GuildJsonStore(config, 200, "welcome")

    production.update(lambda data: {"user": {"step": "production"}})
    testing.update(lambda data: {"user": {"step": "testing"}})

    assert production.read()["user"]["step"] == "production"
    assert testing.read()["user"]["step"] == "testing"
    assert production.path != testing.path


def test_unregistered_guild_is_rejected(config):
    with pytest.raises(StorageError):
        GuildJsonStore(config, 999, "welcome")


@pytest.mark.parametrize("name", ["../welcome", "/tmp/welcome", "", "Welcome"])
def test_unsafe_document_name_is_rejected(config, name):
    with pytest.raises(StorageError):
        GuildJsonStore(config, 200, name)


def test_corrupt_file_is_not_overwritten(config):
    store = GuildJsonStore(config, 200, "welcome")
    store.directory.mkdir(parents=True)
    original = b'{"broken":'
    store.path.write_bytes(original)

    with pytest.raises(StorageError):
        store.update(lambda data: {"new": True})

    assert store.path.read_bytes() == original


def test_wrong_guild_in_file_is_rejected(config):
    store = GuildJsonStore(config, 200, "welcome")
    store.update(lambda data: {"step": "start"})

    envelope = json.loads(store.path.read_text(encoding="utf-8"))
    envelope["guild_id"] = 100
    store.path.write_text(json.dumps(envelope), encoding="utf-8")

    with pytest.raises(StorageError):
        store.read()


def test_unknown_version_is_rejected(config):
    store = GuildJsonStore(config, 200, "welcome")
    store.update(lambda data: {})

    envelope = json.loads(store.path.read_text(encoding="utf-8"))
    envelope["schema_version"] = 999
    store.path.write_text(json.dumps(envelope), encoding="utf-8")

    with pytest.raises(StorageError):
        store.read()


def test_duplicate_keys_are_rejected(config):
    store = GuildJsonStore(config, 200, "welcome")
    store.directory.mkdir(parents=True)
    store.path.write_text(
        '{"schema_version":1,"guild_id":200,"document":"welcome",'
        '"data":{"step":"first","step":"second"}}',
        encoding="utf-8",
    )

    with pytest.raises(StorageError):
        store.read()


def test_non_json_value_does_not_replace_existing_data(config):
    store = GuildJsonStore(config, 200, "welcome")
    store.update(lambda data: {"count": 1})
    original = store.path.read_bytes()

    with pytest.raises(StorageError):
        store.update(lambda data: {"count": float("nan")})

    assert store.path.read_bytes() == original


def test_callback_failure_does_not_change_file(config):
    store = GuildJsonStore(config, 200, "welcome")
    store.update(lambda data: {"count": 1})
    original = store.path.read_bytes()

    def fail(data):
        data["count"] = 999
        raise RuntimeError("simulated failure")

    with pytest.raises(RuntimeError):
        store.update(fail)

    assert store.path.read_bytes() == original


def test_replace_failure_preserves_old_file(config, monkeypatch):
    store = GuildJsonStore(config, 200, "welcome")
    store.update(lambda data: {"count": 1})
    original = store.path.read_bytes()

    def fail_replace(source, destination):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(json_store.os, "replace", fail_replace)

    with pytest.raises(StorageError):
        store.update(lambda data: {"count": 2})

    assert store.path.read_bytes() == original
    assert list(store.directory.glob("*.tmp")) == []


def test_concurrent_updates_across_instances_are_not_lost(config):
    first = GuildJsonStore(config, 200, "counter")
    second = GuildJsonStore(config, 200, "counter")

    def increment(index):
        store = first if index % 2 == 0 else second

        def change(data):
            data["count"] = data.get("count", 0) + 1
            return data

        store.update(change)

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(increment, range(40)))

    assert first.read() == {"count": 40}


def test_read_returns_independent_data(config):
    store = GuildJsonStore(config, 200, "welcome")
    store.update(lambda data: {"user": {"step": "start"}})

    data = store.read()
    data["user"]["step"] = "changed_without_saving"

    assert store.read() == {"user": {"step": "start"}}


def test_integer_dictionary_keys_are_rejected(config):
    store = GuildJsonStore(config, 200, "welcome")

    with pytest.raises(StorageError):
        store.update(lambda data: {123: "value"})

    assert not store.path.exists()
