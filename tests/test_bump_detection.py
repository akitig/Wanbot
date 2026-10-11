from types import SimpleNamespace

import pytest

from wanbot.bump.detection import is_bump_success_message


def message(content=""):
    return SimpleNamespace(
        guild=SimpleNamespace(id=100),
        channel=SimpleNamespace(id=200),
        author=SimpleNamespace(id=300, bot=True),
        content=content,
        embeds=[],
        interaction_metadata=None,
        interaction=None,
    )


def detect(value):
    return is_bump_success_message(
        value,
        guild_id=100,
        channel_id=200,
        disboard_bot_id=300,
    )


@pytest.mark.parametrize(
    "text",
    [
        "Bump done!",
        "BUMP成功しました！",
        "表示順をアップしたよ！",
    ],
)
def test_known_success_text_is_detected(text):
    assert detect(message(text))


@pytest.mark.parametrize("location", ["title", "description", "field"])
def test_success_in_embed_is_detected(location):
    value = message()
    embed = SimpleNamespace(
        title=None,
        description=None,
        fields=[],
    )
    if location == "field":
        embed.fields = [
            SimpleNamespace(name="結果", value="Bump done!"),
        ]
    else:
        setattr(embed, location, "Bump done!")
    value.embeds = [embed]

    assert detect(value)


@pytest.mark.parametrize("target", ["guild", "channel", "author"])
def test_other_scope_or_sender_is_rejected(target):
    value = message("Bump done!")
    getattr(value, target).id = 999

    assert not detect(value)


def test_non_bot_author_is_rejected():
    value = message("Bump done!")
    value.author.bot = False

    assert not detect(value)


def test_dm_is_rejected():
    value = message("Bump done!")
    value.guild = None

    assert not detect(value)


@pytest.mark.parametrize(
    "text",
    [
        "Please wait another 120 minutes.",
        "Bump failed.",
        "BUMPに失敗しました。",
        "BUMPはまだ実行できません。",
        "",
    ],
)
def test_failure_waiting_or_empty_message_is_not_success(text):
    assert not detect(message(text))


def test_command_name_alone_does_not_mean_success():
    value = message("Please wait another 120 minutes.")
    value.interaction_metadata = SimpleNamespace(name="bump")
    value.interaction = SimpleNamespace(name="bump")

    assert not detect(value)


def test_failure_marker_overrides_success_fragment():
    value = message("Bump failed.")
    value.embeds = [SimpleNamespace(description="Bump done!")]

    assert not detect(value)
