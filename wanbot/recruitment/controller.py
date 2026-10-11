from __future__ import annotations

from collections.abc import Callable

import discord

from wanbot.config import GuildConfig
from wanbot.recruitment.confirmation import send_confirmation
from wanbot.recruitment.forms import FormContext, RecruitmentTargetView
from wanbot.recruitment.models import RecruitmentDraft
from wanbot.recruitment.publisher import RecruitmentPublisher


class RecruitmentController:
    def __init__(
        self,
        *,
        guild: GuildConfig,
        publisher: RecruitmentPublisher,
        is_available: Callable[[], bool],
    ) -> None:
        self._guild = guild
        self._publisher = publisher
        self._is_available = is_available
        self._closed = False
        self._views: set[discord.ui.View] = set()

    def _available(self) -> bool:
        return (
            not self._closed
            and self._guild.feature_enabled("recruitment")
            and self._is_available()
        )

    def _remember(self, view: discord.ui.View) -> None:
        if self._closed:
            view.stop()
            return

        self._views = {
            previous
            for previous in self._views
            if not previous.is_finished()
        }
        self._views.add(view)

    async def begin(self, interaction: discord.Interaction) -> None:
        context = FormContext(
            guild_id=self._guild.guild_id,
            channel_id=self._guild.ids["VALO_RECRUIT_CHANNEL_ID"],
            user_id=interaction.user.id,
            is_available=self._available,
        )

        if not await context.check(interaction):
            return

        async def on_draft(
            submitted: discord.Interaction,
            draft: RecruitmentDraft,
        ) -> None:
            confirmation = await send_confirmation(
                submitted,
                draft,
                context=context,
                gachi_role_id=self._guild.ids["GACHI_ROLE_ID"],
                enjoy_role_id=self._guild.ids["ENJOY_ROLE_ID"],
                on_publish=self._publisher.publish,
            )
            if confirmation is not None:
                self._remember(confirmation)

        view = RecruitmentTargetView(
            context=context,
            on_draft=on_draft,
        )
        self._remember(view)

        try:
            await interaction.response.send_message(
                "通知先を選んでください。Gachi・Enjoyの両方も選択できます。",
                view=view,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except BaseException:
            view.stop()
            self._views.discard(view)
            raise

    def close(self) -> None:
        self._closed = True
        for view in self._views:
            view.stop()
        self._views.clear()
