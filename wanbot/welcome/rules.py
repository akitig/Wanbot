"""指定されたルール投稿への本人のリアクションを確認する。"""

from __future__ import annotations

import re

import discord

from wanbot.config import ConfigError, GuildConfig, MAX_DISCORD_ID


CUSTOM_EMOJI = re.compile(r"<a?:[A-Za-z0-9_]+:([0-9]{1,20})>")


class RulesCheckError(RuntimeError):
    """ルールの確認対象を正しく取得できなかった。"""


def emoji_key(value) -> tuple[str, int | str]:
    """カスタム絵文字はID、通常の絵文字は文字列で識別する。"""
    if isinstance(value, (discord.Emoji, discord.PartialEmoji)):
        if value.id is not None:
            return ("custom", value.id)
        value = value.name

    if not isinstance(value, str):
        raise ValueError("Unsupported rules emoji")

    value = value.strip()
    match = CUSTOM_EMOJI.fullmatch(value)
    if match is not None:
        emoji_id = int(match.group(1))
        if not 0 < emoji_id <= MAX_DISCORD_ID:
            raise ValueError("Invalid custom emoji ID")
        return ("custom", emoji_id)

    # 表示形式の違いだけを吸収する。肌色などの違いは残す。
    normalized = value.replace("\ufe0e", "").replace("\ufe0f", "")
    if (
        not normalized
        or "<" in normalized
        or ">" in normalized
        or normalized.isascii()
        or any(character.isspace() for character in normalized)
    ):
        raise ValueError("Invalid rules emoji")

    return ("unicode", normalized)


class RulesReactionChecker:
    def __init__(
        self,
        *,
        client: discord.Client,
        guild_config: GuildConfig,
    ) -> None:
        if not guild_config.feature_enabled("welcome"):
            raise ConfigError("Welcome機能が有効になっていません。")

        try:
            channel_id = guild_config.ids["RULES_CHANNEL_ID"]
            message_id = guild_config.ids["RULES_MESSAGE_ID"]
            expected_emoji = emoji_key(guild_config.rules_emoji)
        except (KeyError, ValueError) as error:
            raise ConfigError(
                "ルールのチャンネル・投稿・絵文字設定を確認してください。"
            ) from error

        self._client = client
        self._guild_id = guild_config.guild_id
        self._channel_id = channel_id
        self._message_id = message_id
        self._expected_emoji = expected_emoji

    @property
    def message_url(self) -> str:
        return (
            f"https://discord.com/channels/{self._guild_id}/"
            f"{self._channel_id}/{self._message_id}"
        )

    async def has_reacted(self, user_id: int) -> bool:
        """確認時点で本人のリアクションがあればTrue。

        通信失敗や対象の不一致は例外とし、未回答とは区別する。
        判定結果はキャッシュしない。
        """
        if type(user_id) is not int or not 0 < user_id <= MAX_DISCORD_ID:
            raise ValueError("Invalid user ID")

        channel = await self._client.fetch_channel(self._channel_id)
        if (
            not isinstance(channel, discord.TextChannel)
            or channel.id != self._channel_id
            or channel.guild.id != self._guild_id
        ):
            raise RulesCheckError("Rules channel does not match")

        message = await channel.fetch_message(self._message_id)
        if (
            message.id != self._message_id
            or message.channel.id != self._channel_id
            or message.guild is None
            or message.guild.id != self._guild_id
        ):
            raise RulesCheckError("Rules message does not match")

        for reaction in message.reactions:
            if emoji_key(reaction.emoji) != self._expected_emoji:
                continue

            reaction_types = [discord.ReactionType.normal]
            if reaction.burst_count:
                reaction_types.append(discord.ReactionType.burst)

            for reaction_type in reaction_types:
                async for user in reaction.users(
                    limit=None,
                    type=reaction_type,
                ):
                    if user.id == user_id:
                        return True

        return False
