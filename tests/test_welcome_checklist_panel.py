import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord

from wanbot.welcome.checklist_panel import ChecklistPanel
from wanbot.welcome.checklist_view import ChecklistState, WelcomeChecklistView


def make_case():
    context = SimpleNamespace(
        guild_id=100,
        channel_id=500,
        session_id="a" * 32,
        check=AsyncMock(return_value=True),
    )
    messages = []
    saved = {}
    state = ChecklistState()

    async def factory():
        return WelcomeChecklistView(
            context=context,
            state=state,
            rules_url="https://discord.com/channels/100/600/700",
            rules_emoji="👍",
            on_introduction=AsyncMock(),
            on_diagnosis=AsyncMock(),
            on_interview=AsyncMock(),
        )

    channel = Mock(spec=discord.TextChannel)
    channel.id = 500
    channel.guild = SimpleNamespace(id=100)
    channel.topic = f"wanbot:welcome:100:{context.session_id}"

    async def send(**kwargs):
        message = SimpleNamespace(
            id=1000 + len(messages),
            author=SimpleNamespace(id=900),
            channel=channel,
            webhook_id=None,
            components=kwargs["view"].children,
            edit=AsyncMock(),
            delete=AsyncMock(),
        )
        messages.append(message)
        return message

    async def fetch(message_id):
        return next(item for item in messages if item.id == message_id)

    async def history(*, limit):
        items = list(reversed(messages))
        if limit is not None:
            items = items[:limit]
        for item in items:
            yield item

    async def get(key):
        return saved.get(key)

    async def save(key, value):
        saved[key] = value

    channel.send = AsyncMock(side_effect=send)
    channel.fetch_message = AsyncMock(side_effect=fetch)
    channel.history = history
    panel = ChecklistPanel(
        client=SimpleNamespace(
            user=SimpleNamespace(id=900),
            fetch_channel=AsyncMock(return_value=channel),
        ),
        context=context,
        repository=SimpleNamespace(
            get=AsyncMock(side_effect=get),
            save=AsyncMock(side_effect=save),
        ),
        view=discord.ui.View(),
        view_factory=factory,
    )
    return panel, channel, messages


def test_refresh_reuses_existing_post():
    async def scenario():
        panel, channel, messages = make_case()
        try:
            first = await panel.ensure()
            second = await panel.ensure()
            assert first == second
            channel.send.assert_awaited_once()
            options = messages[0].edit.call_args.kwargs
            assert options["content"] is None
            assert options["embeds"] == []
        finally:
            panel.close()

    asyncio.run(scenario())


def test_already_at_bottom_does_not_create_another_post():
    async def scenario():
        panel, channel, _ = make_case()
        try:
            await panel.ensure()
            await panel.ensure(move_to_bottom=True)
            channel.send.assert_awaited_once()
        finally:
            panel.close()

    asyncio.run(scenario())


def test_completion_moves_checklist_after_result():
    async def scenario():
        panel, channel, messages = make_case()
        try:
            await panel.ensure()
            old = messages[0]
            # 診断結果など、チェックリストの後に投稿が追加された状態。
            messages.append(SimpleNamespace(id=2000))
            await panel.ensure(move_to_bottom=True)
            assert channel.send.await_count == 2
            old.delete.assert_awaited_once()
        finally:
            panel.close()

    asyncio.run(scenario())
