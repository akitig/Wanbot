from __future__ import annotations

from collections.abc import Callable

import discord

from wanbot.config import GuildConfig
from wanbot.recruitment.models import RecruitmentDraft
from wanbot.recruitment.presentation import build_post


class RecruitmentPublishError(RuntimeError):
    """募集の投稿条件を満たしていない場合のエラー。"""


class RecruitmentPublisher:
    def __init__(
        self,
        *,
        client: discord.Client,
        guild: GuildConfig,
        is_available: Callable[[], bool],
    ) -> None:
        self._client = client
        self._guild = guild
        self._is_available = is_available

    async def publish(
        self,
        interaction: discord.Interaction,
        draft: RecruitmentDraft,
    ) -> None:
        config = self._guild
        channel_id = config.ids.get("VALO_RECRUIT_CHANNEL_ID")

        if (
            not config.feature_enabled("recruitment")
            or not self._is_available()
            or interaction.guild_id != config.guild_id
            or interaction.channel_id != channel_id
            or interaction.user.bot
        ):
            raise RecruitmentPublishError("Recruitment is unavailable")

        guild = self._client.get_guild(config.guild_id)
        channel = self._client.get_channel(channel_id)

        if (
            guild is None
            or guild.unavailable
            or guild.me is None
            or not isinstance(channel, discord.TextChannel)
            or channel.guild.id != config.guild_id
        ):
            raise RecruitmentPublishError("Recruitment channel is unavailable")

        permissions = channel.permissions_for(guild.me)
        if not (
            permissions.view_channel
            and permissions.send_messages
            and permissions.embed_links
        ):
            raise RecruitmentPublishError("Missing channel permissions")

        post = build_post(
            draft,
            author_id=interaction.user.id,
            gachi_role_id=config.ids["GACHI_ROLE_ID"],
            enjoy_role_id=config.ids["ENJOY_ROLE_ID"],
        )

        # 投稿に含めるロールだけを確認する。
        for target in post.allowed_mentions.roles:
            role = guild.get_role(target.id)

            if (
                role is None
                or role.guild.id != config.guild_id
                or role.id == config.guild_id
            ):
                raise RecruitmentPublishError("Invalid recruitment role")

            if not role.mentionable and not permissions.mention_everyone:
                raise RecruitmentPublishError("Missing role mention permission")

        # 自動再送はしない。失敗は確認画面の処理へ伝える。
        await channel.send(
            content=post.content,
            embed=post.embed,
            allowed_mentions=post.allowed_mentions,
        )
