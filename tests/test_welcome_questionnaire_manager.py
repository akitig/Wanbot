import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from wanbot.config import parse_config
from wanbot.welcome.questionnaire_manager import WelcomeQuestionnaires
import wanbot.welcome.questionnaire_manager as manager_module
from wanbot.welcome.questionnaire_repository import QuestionnaireRepository
from wanbot.welcome.repository import WelcomeRepository


async def make_case(tmp_path, monkeypatch):
    config = parse_config({
        "DISCORD_TOKEN": "test-token",
        "PROD_GUILD_ID": "101",
        "TEST_GUILD_ID": "100",
        "RUNTIME_DATA_DIR": str(tmp_path / "runtime"),
    })
    welcome = WelcomeRepository(config, 100)
    questions = QuestionnaireRepository(config, 100)
    session = await welcome.begin(300)
    session = await welcome.attach_channel(session.session_id, 500)

    panel = SimpleNamespace(ensure=AsyncMock(return_value=700), close=Mock())
    factory = Mock(return_value=panel)
    monkeypatch.setattr(manager_module, "QuestionnairePanel", factory)

    manager = WelcomeQuestionnaires(
        client=Mock(),
        guild_id=100,
        welcome_repository=welcome,
        questionnaire_repository=questions,
        panel_repository=Mock(),
        lock=asyncio.Lock(),
        is_available=lambda: True,
    )
    return SimpleNamespace(
        manager=manager,
        welcome=welcome,
        questions=questions,
        session=session,
        panel=panel,
        factory=factory,
    )


async def close_case(item):
    await item.manager.close()
    await item.questions.close()
    await item.welcome.close()


def test_refresh_restores_once_without_reposting(tmp_path, monkeypatch):
    async def run():
        item = await make_case(tmp_path, monkeypatch)
        try:
            await item.manager.refresh()
            await item.manager.refresh()

            item.factory.assert_called_once()
            item.panel.ensure.assert_awaited_once()
        finally:
            await close_case(item)

    asyncio.run(run())


def test_failed_panel_is_retried(tmp_path, monkeypatch):
    async def run():
        item = await make_case(tmp_path, monkeypatch)
        try:
            item.panel.ensure.side_effect = OSError("temporary failure")
            await item.manager.refresh()

            item.panel.ensure.side_effect = None
            item.panel.ensure.return_value = 700
            await item.manager.refresh()
            await item.manager.refresh()

            assert item.panel.ensure.await_count == 2
            item.factory.assert_called_once()
        finally:
            await close_case(item)

    asyncio.run(run())


def test_archiving_session_stops_existing_panel(tmp_path, monkeypatch):
    async def run():
        item = await make_case(tmp_path, monkeypatch)
        try:
            await item.manager.refresh()
            view = item.factory.call_args.kwargs["view"]

            await item.welcome.begin_archive(
                item.session.session_id, reason="member_left"
            )
            await item.manager.refresh()

            assert view.is_finished()
            item.panel.close.assert_called_once()
        finally:
            await close_case(item)

    asyncio.run(run())


def test_closed_session_does_not_get_panel(tmp_path, monkeypatch):
    async def run():
        item = await make_case(tmp_path, monkeypatch)
        try:
            await item.welcome.close_session(
                item.session.session_id, reason="member_left"
            )

            assert await item.manager.ensure(item.session) is None
            item.factory.assert_not_called()
        finally:
            await close_case(item)

    asyncio.run(run())


def test_start_button_is_connected_to_saved_questions(tmp_path, monkeypatch):
    async def run():
        item = await make_case(tmp_path, monkeypatch)
        try:
            await item.manager.refresh()
            view = item.factory.call_args.kwargs["view"]
            interaction = SimpleNamespace(
                guild_id=100,
                channel_id=500,
                user=SimpleNamespace(id=300),
                response=SimpleNamespace(
                    is_done=Mock(return_value=True),
                    defer=AsyncMock(),
                    send_message=AsyncMock(),
                ),
                followup=SimpleNamespace(send=AsyncMock()),
                edit_original_response=AsyncMock(),
            )

            await view.begin(interaction)

            saved = await item.questions.get(item.session.session_id)
            assert saved.progress.next_question == "name"
            shown = interaction.edit_original_response.call_args.kwargs["view"]
            assert shown.question == "name"
        finally:
            await close_case(item)

    asyncio.run(run())
