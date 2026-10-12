"""サーバーごとの退出情報と、通知先メッセージを保存する。"""

from copy import deepcopy

from wanbot.leave_log.models import decode_snapshot, encode_snapshot
from wanbot.storage.async_store import AsyncGuildJsonStore


class DepartureConflict(ValueError):
    pass


def _id(value):
    if type(value) is not int or not 0 < value < 2**64:
        raise DepartureConflict("Discord IDが不正です。")


class DepartureRepository:
    def __init__(self, config, guild_id, *, store=None):
        _id(guild_id)
        self._guild_id = guild_id
        self._store = (
            store if store is not None else
            AsyncGuildJsonStore(config, guild_id, "leave_log_events")
        )

    def _records(self, data):
        if data == {}:
            return {}
        if (
            type(data) is not dict
            or set(data) != {"version", "records"}
            or type(data["version"]) is not int
            or data["version"] != 1
            or type(data["records"]) is not dict
        ):
            raise DepartureConflict("退出記録の保存形式が不正です。")

        for event_id, record in data["records"].items():
            if (
                type(record) is not dict
                or set(record) != {"snapshot", "channel_id", "message_id"}
            ):
                raise DepartureConflict("退出記録の内容が不正です。")

            snapshot = decode_snapshot(record["snapshot"])
            if (
                snapshot.event_id != event_id
                or snapshot.guild_id != self._guild_id
            ):
                raise DepartureConflict("退出記録の所属が一致しません。")

            channel_id = record["channel_id"]
            message_id = record["message_id"]
            if (channel_id is None) != (message_id is None):
                raise DepartureConflict("退出通知の送信記録が不正です。")
            if channel_id is not None:
                _id(channel_id)
                _id(message_id)

        return data["records"]

    async def add(self, snapshot):
        if snapshot.guild_id != self._guild_id:
            raise DepartureConflict("退出記録のサーバーが一致しません。")
        encoded = encode_snapshot(snapshot)
        candidate = {
            "snapshot": encoded,
            "channel_id": None,
            "message_id": None,
        }
        self._records({
            "version": 1,
            "records": {snapshot.event_id: candidate},
        })
        result = None

        def change(data):
            nonlocal result
            records = dict(self._records(data))
            previous = records.get(snapshot.event_id)
            if previous is not None:
                if previous["snapshot"] != encoded:
                    raise DepartureConflict(
                        "同じ識別子に別の退出情報が保存されています。"
                    )
                result = deepcopy(previous)
                return data

            records[snapshot.event_id] = candidate
            result = deepcopy(candidate)
            return {"version": 1, "records": records}

        await self._store.update(change)
        return result

    async def get(self, event_id):
        record = self._records(await self._store.read()).get(event_id)
        return None if record is None else deepcopy(record)

    async def pending(self):
        records = self._records(await self._store.read())
        return tuple(
            deepcopy(record)
            for record in records.values()
            if record["message_id"] is None
        )

    async def mark_sent(self, event_id, *, channel_id, message_id):
        _id(channel_id)
        _id(message_id)
        result = None

        def change(data):
            nonlocal result
            records = dict(self._records(data))
            previous = records.get(event_id)
            if previous is None:
                raise DepartureConflict("退出記録が見つかりません。")

            if previous["message_id"] is not None:
                if (
                    previous["channel_id"] != channel_id
                    or previous["message_id"] != message_id
                ):
                    raise DepartureConflict(
                        "別の通知メッセージがすでに記録されています。"
                    )
                result = deepcopy(previous)
                return data

            updated = {
                **previous,
                "channel_id": channel_id,
                "message_id": message_id,
            }
            records[event_id] = updated
            result = deepcopy(updated)
            return {"version": 1, "records": records}

        await self._store.update(change)
        return result

    async def close(self):
        await self._store.close()
