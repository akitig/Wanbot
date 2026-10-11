import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from wanbot.config import parse_config
from wanbot.welcome.channels import WelcomeChannelError
from wanbot.welcome.questionnaire import (
    Questionnaire,
    answer,
    edit_text_answer,
)
from wanbot.welcome.questionnaire_repository import QuestionnaireSnapshot
from wanbot.welcome.questionnaire_result import (
    QuestionnaireResult,
    QuestionnaireResultRepository,
)


def completed_snapshot():
    progress = Questionnaire()
    responses = (
        ("name", "おおかみ"),
        ("gender", "no_answer"),
        ("adult", "yes"),
        ("main_game", "VALORANT"),
        ("comment", "@everyone よろしくお願いします"),
        ("hobby", "旅行"),
        ("plays_valo", "yes"),
    )
    for question, value in responses:
        progress = answer(progress, question, value)

    return QuestionnaireSnapshot(
        session_id="a" * 32,
        user_id=300,
        revision=7,
        progress=progress,
    )


def make_case(tmp_path):
    config = parse_config({
        "DISCORD_TOKEN": "test-token",
        "PROD_GUILD_ID": "101",
        "TEST_GUILD_ID": "100",
        "RUNTIME_DATA_DIR": str(tmp_path / "runtime"),
    })
    context = SimpleNamespace(
        guild_id=100,
        channel_id=500,
        user_id=300,
        session_id="a" * 32,
    )
    repository = QuestionnaireResultRepository(config, 100)
    channel = Mock(spec=discord.TextChannel)
    channel.id = 500
    channel.guild = SimpleNamespace(id=100)
    channel.topic = f"wanbot:welcome:100:{context.session_id}"
    messages = []

    async def send(**kwargs):
        message = SimpleNamespace(
            id=700,
            author=SimpleNamespace(id=900),
            channel=channel,
            webhook_id=None,
            embeds=[kwargs["embed"]],
        )

        async def edit(**updated):
            message.embeds = [updated["embed"]]
            return message

        message.edit = AsyncMock(side_effect=edit)
        messages.append(message)
        return message

    async def fetch(message_id):
        return next(item for item in messages if item.id == message_id)

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
    publisher = QuestionnaireResult(
        client=client,
        context=context,
        repository=repository,
    )
    return SimpleNamespace(
        repository=repository,
        channel=channel,
        messages=messages,
        publisher=publisher,
    )


def test_result_is_posted_without_mentions(tmp_path):
    async def run():
        item = make_case(tmp_path)
        try:
            assert await item.publisher.publish(completed_snapshot()) == 700

            kwargs = item.channel.send.call_args.kwargs
            mentions = kwargs["allowed_mentions"]
            assert mentions.everyone is False
            assert mentions.users is False
            assert mentions.roles is False
            assert len(kwargs["embed"].fields) == 7
        finally:
            await item.repository.close()

    asyncio.run(run())


def test_repeated_publish_updates_same_message(tmp_path):
    async def run():
        item = make_case(tmp_path)
        try:
            original = completed_snapshot()
            await item.publisher.publish(original)

            updated = replace(
                original,
                revision=8,
                progress=edit_text_answer(
                    original.progress, "hobby", "読書"
                ),
            )
            await item.publisher.publish(updated)

            item.channel.send.assert_awaited_once()
            item.messages[0].edit.assert_awaited_once()
            fields = item.messages[0].embeds[0].fields
            assert next(field.value for field in fields if field.name == "リアルの趣味") == "読書"
        finally:
            await item.repository.close()

    asyncio.run(run())


def test_save_failure_recovers_existing_post(tmp_path):
    async def run():
        item = make_case(tmp_path)
        original_save = item.repository.save
        item.repository.save = AsyncMock(side_effect=OSError("save failed"))
        try:
            with pytest.raises(OSError):
                await item.publisher.publish(completed_snapshot())

            item.repository.save = original_save
            await item.publisher.publish(completed_snapshot())

            item.channel.send.assert_awaited_once()
            assert await item.repository.get("a" * 32) == 700
        finally:
            await item.repository.close()

    asyncio.run(run())


def test_other_users_saved_message_is_not_modified(tmp_path):
    async def run():
        item = make_case(tmp_path)
        try:
            await item.publisher.publish(completed_snapshot())
            item.messages[0].author.id = 999

            with pytest.raises(WelcomeChannelError):
                await item.publisher.publish(completed_snapshot())

            item.messages[0].edit.assert_not_awaited()
        finally:
            await item.repository.close()

    asyncio.run(run())


def test_wrong_snapshot_is_rejected(tmp_path):
    async def run():
        item = make_case(tmp_path)
        try:
            wrong = replace(completed_snapshot(), user_id=301)

            with pytest.raises(WelcomeChannelError):
                await item.publisher.publish(wrong)

            item.channel.send.assert_not_awaited()
        finally:
            await item.repository.close()

    asyncio.run(run())
