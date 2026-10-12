"""本人期限によるキック前のDM通知と、送信結果の保存。"""

import re
from datetime import datetime, timezone

import discord

from wanbot.storage.async_store import AsyncGuildJsonStore


class TimeoutNoticeError(ValueError):
    pass


def _key(value):
    return (
        type(value) is str
        and re.fullmatch(r"[0-9a-f]{32}", value) is not None
    )


def _id(value):
    return type(value) is int and 0 < value < 2**64


class TimeoutNotice:
    def __init__(self, config, guild_id, *, store=None):
        self._guild_id = guild_id
        self._store = (
            store if store is not None
            else AsyncGuildJsonStore(config, guild_id, "welcome_timeout_notices")
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
            raise TimeoutNoticeError("DM通知の保存形式が不正です。")

        fields = {
            "request_id", "guild_id", "session_id", "user_id",
            "channel_id", "status", "attempted_at", "error_type",
        }
        for key, record in data["records"].items():
            if (
                type(record) is not dict
                or set(record) != fields
                or not _key(key)
                or record["request_id"] != key
                or not _key(record["session_id"])
                or record["guild_id"] != self._guild_id
                or any(
                    not _id(record[name])
                    for name in ("guild_id", "user_id", "channel_id")
                )
                or record["status"] not in {
                    "attempted", "sent", "failed", "unknown"
                }
            ):
                raise TimeoutNoticeError("DM通知の保存内容が不正です。")

            try:
                timestamp = datetime.fromisoformat(record["attempted_at"])
                if timestamp.utcoffset() is None:
                    raise ValueError
            except (TypeError, ValueError):
                raise TimeoutNoticeError("DM通知の日時が不正です。") from None

            error_type = record["error_type"]
            if record["status"] in ("attempted", "sent"):
                if error_type is not None:
                    raise TimeoutNoticeError("DM通知の結果が不正です。")
            elif (
                type(error_type) is not str
                or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", error_type) is None
            ):
                raise TimeoutNoticeError("DM通知のエラー種別が不正です。")

        return data["records"]

    async def get(self, request_id):
        if not _key(request_id):
            raise TimeoutNoticeError("確認依頼の識別子が不正です。")
        record = self._records(await self._store.read()).get(request_id)
        return None if record is None else dict(record)

    async def notify(self, request, member):
        """承認済み対象の再確認後、Welcome共通ロック内で呼ぶ。"""
        if (
            request["status"] != "approved"
            or "waiting_timeout" not in request
            or request["guild_id"] != self._guild_id
            or member.id != request["user_id"]
            or member.guild.id != self._guild_id
        ):
            raise TimeoutNoticeError("承認済みの期限切れ依頼と対象者が必要です。")

        identity = {
            name: request[name]
            for name in (
                "request_id", "guild_id", "session_id", "user_id", "channel_id"
            )
        }
        candidate = {
            **identity,
            "status": "attempted",
            "attempted_at": datetime.now(timezone.utc).isoformat(),
            "error_type": None,
        }
        self._records({
            "version": 1,
            "records": {request["request_id"]: candidate},
        })
        reserved = False
        result = None

        def reserve(data):
            nonlocal reserved, result
            records = self._records(data)
            existing = records.get(request["request_id"])
            if existing is not None:
                if any(existing[name] != value for name, value in identity.items()):
                    raise TimeoutNoticeError("DM通知の対象が一致しません。")
                result = dict(existing)
                return data
            records[request["request_id"]] = candidate
            reserved = True
            result = dict(candidate)
            return {"version": 1, "records": records}

        # 先に試行を保存する。途中停止後は、結果不明のDMを再送しない。
        await self._store.update(reserve)
        if not reserved:
            return result

        status = "sent"
        error_type = None
        try:
            await member.send(
                "参加手続きで本人の対応待ちが設定された期限に達したため、"
                "管理者の承認を受けて、サーバーからの退出処理を行います。\n"
                "参加をご希望の場合は、手続きを進められるときに"
                "再度サーバーへ参加してください。",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.NotFound) as error:
            status = "failed"
            error_type = type(error).__name__
        except Exception as error:
            # 通信切断などでは、相手に届いたか断定できない。
            status = "unknown"
            name = type(error).__name__
            error_type = (
                name
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name)
                else "Exception"
            )

        def finish(data):
            nonlocal result
            records = self._records(data)
            current = records.get(request["request_id"])
            if current is None or current["status"] != "attempted":
                raise TimeoutNoticeError("DM通知の状態が変わっています。")
            result = {
                **current, "status": status, "error_type": error_type
            }
            records[request["request_id"]] = result
            updated = {"version": 1, "records": records}
            self._records(updated)
            return updated

        await self._store.update(finish)
        return dict(result)

    async def close(self):
        await self._store.close()
