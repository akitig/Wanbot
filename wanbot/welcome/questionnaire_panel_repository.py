"""Welcomeの開始パネルの投稿IDを保存する。"""

import re

from wanbot.storage.async_store import AsyncGuildJsonStore


class QuestionnairePanelDataError(ValueError):
    pass


def _session_id(value):
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[0-9a-f]{32}", value) is None
    ):
        raise ValueError("Invalid Welcome session ID")


def _message_id(value):
    if type(value) is not int or not 0 < value < 1 << 64:
        raise ValueError("Invalid Discord message ID")


def _decode(data):
    if data == {}:
        return {}

    try:
        if (
            not isinstance(data, dict)
            or set(data) != {"state_version", "panels"}
            or type(data["state_version"]) is not int
            or data["state_version"] != 1
            or not isinstance(data["panels"], dict)
        ):
            raise ValueError

        panels = data["panels"]
        for session_id, message_id in panels.items():
            _session_id(session_id)
            _message_id(message_id)

        if len(set(panels.values())) != len(panels):
            raise ValueError

        return dict(panels)
    except (ValueError, TypeError):
        raise QuestionnairePanelDataError(
            "Invalid Welcome questionnaire panel data"
        ) from None


class QuestionnairePanelRepository:
    def __init__(self, config, guild_id):
        self._store = AsyncGuildJsonStore(
            config, guild_id, "welcome_questionnaire_panels"
        )

    async def get(self, session_id):
        _session_id(session_id)
        return _decode(await self._store.read()).get(session_id)

    async def save(self, session_id, message_id):
        _session_id(session_id)
        _message_id(message_id)

        def change(data):
            panels = _decode(data)
            if any(
                key != session_id and value == message_id
                for key, value in panels.items()
            ):
                raise QuestionnairePanelDataError(
                    "Panel belongs to another Welcome session"
                )
            panels[session_id] = message_id
            return {"state_version": 1, "panels": panels}

        await self._store.update(change)

    async def close(self):
        await self._store.close()
