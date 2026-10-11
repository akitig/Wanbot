import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from wanbot.config import parse_config
from wanbot.welcome.channels import WelcomeChannelError
from wanbot.welcome.questionnaire_forms import (
    QuestionnaireContext,
    QuestionnaireStartView,
)
from wanbot.welcome.questionnaire_panel import QuestionnairePanel
from wanbot.welcome.questionnaire_panel_repository import (
    QuestionnairePanelDataError,
    QuestionnairePanelRepository,
)


def make_case(tmp_path):
    config = parse_config({
        "DISCORD_TOKEN": "test-token",
        "PROD_GUILD_ID": "101",
        "TEST_GUILD_ID": "100",
        "RUNTIME_DATA_DIR": str(tmp_path / "runtime"),
    })
    context = QuestionnaireContext(
        guild_id=100,
        channel_id=500,
        user_id=300,
        session_id="a" * 32,
        is_available=lambda: True,
    )
    repository = QuestionnairePanelRepository(config, 100)
    view = QuestionnaireStartView(context, on_begin=AsyncMock())

    channel = Mock(spec=discord.TextChannel)
    channel.id = 500
    channel.guild = SimpleNamespace(id=100)
    channel.topic = f"wanbot:welcome:100:{context.session_id}"
    messages = []

    async def send(content, **kwargs):
        message = SimpleNamespace(
            id=700 + len(messages),
            author=SimpleNamespace(id=900),
            channel=channel,
            webhook_id=None,
            components=[
                SimpleNamespace(children=[
                    SimpleNamespace(
                        custom_id=view.children[0].custom_id
                    )
                ])
            ],
            edit=AsyncMock(),
        )
        messages.append(message)
        return message

    async def fetch(message_id):
        for message in messages:
            if message.id == message_id:
                return message
        raise discord.NotFound(
            SimpleNamespace(status=404, reason="test"),
            {"code": 10008, "message": "Unknown Message"},
        )

    async def history(**kwargs):
        for message in reversed(messages):
            yield message

    channel.send = AsyncMock(side_effect=send)
    channel.fetch_message = AsyncMock(side_effect=fetch)
    channel.history = history

    client = SimpleNamespace(
        user=SimpleNamespace(id=900),
        fetch_channel=AsyncMock(return_value=channel),
    )
    panel = QuestionnairePanel(
        client=client,
        context=context,
        repository=repository,
        view=view,
    )
    return SimpleNamespace(
        config=config,
        context=context,
        repository=repository,
        view=view,
        channel=channel,
        client=client,
        panel=panel,
        messages=messages,
    )


def test_repeated_ensure_does_not_duplicate_panel(tmp_path):
    async def run():
        item = make_case(tmp_path)
        try:
            first = await item.panel.ensure()
            second = await item.panel.ensure()

            assert first == second == 700
            item.channel.send.assert_awaited_once()
            assert await item.repository.get("a" * 32) == 700
        finally:
            item.panel.close()
            await item.repository.close()

    asyncio.run(run())


def test_reopening_reuses_saved_message(tmp_path):
    async def run():
        item = make_case(tmp_path)
        await item.panel.ensure()
        item.panel.close()
        await item.repository.close()

        repository = QuestionnairePanelRepository(item.config, 100)
        view = QuestionnaireStartView(
            item.context, on_begin=AsyncMock()
        )
        panel = QuestionnairePanel(
            client=item.client,
            context=item.context,
            repository=repository,
            view=view,
        )
        try:
            assert await panel.ensure() == 700
            item.channel.send.assert_awaited_once()
            assert item.messages[0].edit.call_args.kwargs["view"] is view
        finally:
            panel.close()
            await repository.close()

    asyncio.run(run())


def test_history_recovers_after_save_failure(tmp_path):
    async def run():
        item = make_case(tmp_path)
        original = item.repository.save
        item.repository.save = AsyncMock(
            side_effect=OSError("save failed")
        )
        try:
            with pytest.raises(OSError):
                await item.panel.ensure()

            assert len(item.messages) == 1
            item.repository.save = original

            assert await item.panel.ensure() == 700
            item.channel.send.assert_awaited_once()
        finally:
            item.panel.close()
            await item.repository.close()

    asyncio.run(run())


def test_deleted_panel_is_recreated(tmp_path):
    async def run():
        item = make_case(tmp_path)
        try:
            await item.panel.ensure()
            item.messages.clear()

            await item.panel.ensure()

            assert item.channel.send.await_count == 2
            assert len(item.messages) == 1
        finally:
            item.panel.close()
            await item.repository.close()

    asyncio.run(run())


def test_wrong_saved_message_is_not_modified(tmp_path):
    async def run():
        item = make_case(tmp_path)
        try:
            await item.panel.ensure()
            item.messages[0].author.id = 999

            with pytest.raises(WelcomeChannelError):
                await item.panel.ensure()

            item.messages[0].edit.assert_not_awaited()
            item.channel.send.assert_awaited_once()
        finally:
            item.panel.close()
            await item.repository.close()

    asyncio.run(run())


def test_wrong_channel_topic_prevents_posting(tmp_path):
    async def run():
        item = make_case(tmp_path)
        try:
            item.channel.topic = "unrelated"

            with pytest.raises(WelcomeChannelError):
                await item.panel.ensure()

            item.channel.send.assert_not_awaited()
        finally:
            item.panel.close()
            await item.repository.close()

    asyncio.run(run())


def test_corrupt_panel_data_is_not_overwritten(tmp_path):
    async def run():
        item = make_case(tmp_path)
        try:
            corrupt = {"state_version": 999, "panels": {}}
            await item.repository._store.update(lambda data: corrupt)

            with pytest.raises(QuestionnairePanelDataError):
                await item.panel.ensure()

            assert await item.repository._store.read() == corrupt
            item.channel.send.assert_not_awaited()
        finally:
            item.panel.close()
            await item.repository.close()

    asyncio.run(run())


def test_concurrent_ensure_posts_once(tmp_path):
    async def run():
        item = make_case(tmp_path)
        try:
            results = await asyncio.gather(
                item.panel.ensure(),
                item.panel.ensure(),
            )

            assert results == [700, 700]
            item.channel.send.assert_awaited_once()
        finally:
            item.panel.close()
            await item.repository.close()

    asyncio.run(run())
