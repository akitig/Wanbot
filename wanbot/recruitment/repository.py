from __future__ import annotations

from wanbot.config import AppConfig
from wanbot.storage.async_store import AsyncGuildJsonStore


class RecruitmentPanelDataError(ValueError):
    """保存された募集パネル情報が不正な場合のエラー。"""


def _valid_id(value: object) -> bool:
    return type(value) is int and 0 < value < 1 << 64


def _decode(data: dict) -> int | None:
    if data == {}:
        return None

    if (
        set(data) != {"state_version", "panel_message_id"}
        or type(data["state_version"]) is not int
        or data["state_version"] != 1
        or not _valid_id(data["panel_message_id"])
    ):
        raise RecruitmentPanelDataError("Invalid recruitment panel data")

    return data["panel_message_id"]


class RecruitmentPanelRepository:
    def __init__(self, config: AppConfig, guild_id: int) -> None:
        self._store = AsyncGuildJsonStore(
            config,
            guild_id,
            "recruitment_panel",
        )

    async def read(self) -> int | None:
        return _decode(await self._store.read())

    async def set_panel_message_id(self, message_id: int) -> None:
        if not _valid_id(message_id):
            raise ValueError("Invalid panel message ID")

        def change(data: dict) -> dict:
            # 不正な保存データを黙って上書きしない。
            _decode(data)
            return {
                "state_version": 1,
                "panel_message_id": message_id,
            }

        await self._store.update(change)

    async def close(self) -> None:
        await self._store.close()
