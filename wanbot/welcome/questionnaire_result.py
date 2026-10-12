"""参加者への質問の回答結果をWelcome内へ共有する。"""

import asyncio

import discord

from wanbot.storage.async_store import AsyncGuildJsonStore
from wanbot.welcome.channels import WelcomeChannelError
from wanbot.welcome.questionnaire_panel_repository import (
    QuestionnairePanelRepository,
)


RESULT_TITLE = "参加者への質問：回答内容"


def build_result_embed(snapshot):
    progress = snapshot.progress
    if progress.status != "completed":
        raise ValueError("Questionnaire is not completed")

    genders = {
        "female": "女性",
        "male": "男性",
        "no_answer": "回答しない",
    }
    embed = discord.Embed(
        title=RESULT_TITLE,
        colour=discord.Colour.green(),
    )
    fields = (
        ("名前", progress.name),
        ("性別", genders[progress.gender]),
        ("25歳以上ですか？", "Yes"),
        ("メインのゲーム", progress.main_game),
        ("ひとこと", progress.comment),
        ("リアルの趣味", progress.hobby),
        ("VALORANTをやりますか？", "Yes" if progress.plays_valo else "No"),
    )
    for label, value in fields:
        embed.add_field(name=label, value=value, inline=False)
    return embed


class QuestionnaireResultRepository(QuestionnairePanelRepository):
    def __init__(self, config, guild_id):
        self._store = AsyncGuildJsonStore(
            config, guild_id, "welcome_questionnaire_results"
        )


class QuestionnaireResult:
    def __init__(self, *, client, context, repository):
        self._client = client
        self._context = context
        self._repository = repository
        self._lock = asyncio.Lock()

    def _matches(self, message):
        return (
            message.author.id == self._client.user.id
            and message.channel.id == self._context.channel_id
            and message.webhook_id is None
            and any(
                embed.title == RESULT_TITLE
                for embed in message.embeds
            )
        )

    async def publish(self, snapshot):
        if (
            snapshot.session_id != self._context.session_id
            or snapshot.user_id != self._context.user_id
        ):
            raise WelcomeChannelError("Questionnaire result does not match")

        embed = build_result_embed(snapshot)

        async with self._lock:
            channel = await self._client.fetch_channel(
                self._context.channel_id
            )
            if (
                self._client.user is None
                or not isinstance(channel, discord.TextChannel)
                or channel.id != self._context.channel_id
                or channel.guild.id != self._context.guild_id
                or channel.topic != (
                    f"wanbot:welcome:{self._context.guild_id}:"
                    f"{self._context.session_id}"
                )
            ):
                raise WelcomeChannelError("Invalid result channel")

            message_id = await self._repository.get(snapshot.session_id)
            message = None

            if message_id is not None:
                try:
                    message = await channel.fetch_message(message_id)
                except discord.NotFound as error:
                    if error.code != 10008:
                        raise

                if message is not None and not self._matches(message):
                    raise WelcomeChannelError(
                        "Saved questionnaire result does not match"
                    )

            if message is None:
                # 投稿成功後、保存前に停止した場合の復旧。
                # この案内専用チャンネル内の、Bot自身の結果投稿を探す。
                async for candidate in channel.history(limit=None):
                    if self._matches(candidate):
                        message = candidate
                        break

            if message is None:
                message = await channel.send(
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            else:
                await message.edit(
                    content=None,
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions.none(),
                )

            await self._repository.save(snapshot.session_id, message.id)
            return message.id
