from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import discord

from wanbot.logging_config import log_event
from wanbot.recruitment.models import (
    TARGET_NAMES,
    RecruitmentDraft,
    RecruitmentInputError,
    make_draft,
)


DraftHandler = Callable[
    [discord.Interaction, RecruitmentDraft],
    Awaitable[None],
]


async def notice(
    interaction: discord.Interaction,
    content: str,
) -> None:
    kwargs = {
        "ephemeral": True,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if interaction.response.is_done():
        await interaction.followup.send(content, **kwargs)
    else:
        await interaction.response.send_message(content, **kwargs)


async def report_error(
    interaction: discord.Interaction,
    error: Exception,
    guild_id: int,
) -> None:
    log_event(
        "recruitment",
        "form_failed",
        guild_id=guild_id,
        level=logging.ERROR,
        error=error,
    )
    try:
        await notice(
            interaction,
            "入力画面の処理に失敗しました。募集パネルからやり直してください。",
        )
    except discord.HTTPException as notification_error:
        log_event(
            "recruitment",
            "error_notice_failed",
            guild_id=guild_id,
            level=logging.WARNING,
            error=notification_error,
        )


@dataclass(frozen=True)
class FormContext:
    guild_id: int
    channel_id: int
    user_id: int
    is_available: Callable[[], bool]

    async def check(self, interaction: discord.Interaction) -> bool:
        if (
            interaction.guild_id != self.guild_id
            or interaction.channel_id != self.channel_id
            or interaction.user.id != self.user_id
        ):
            await notice(
                interaction,
                "この入力画面は、開始した本人が元のチャンネルで操作してください。",
            )
            return False

        if not self.is_available():
            await notice(
                interaction,
                "現在、募集機能を利用できません。",
            )
            return False

        return True


class RecruitmentModal(discord.ui.Modal):
    def __init__(
        self,
        *,
        context: FormContext,
        targets: frozenset[str],
        on_draft: DraftHandler,
    ) -> None:
        super().__init__(title="VALORANT募集の入力", timeout=300)

        if not targets or not targets <= TARGET_NAMES:
            raise RecruitmentInputError("通知先が不正です。")

        self.context = context
        self.targets = frozenset(targets)
        self._on_draft = on_draft

        self.modes = discord.ui.TextInput(
            label="募集モード（複数記入可）",
            placeholder="例：コンペ、アンレート",
            required=True,
            max_length=200,
        )
        self.player_count = discord.ui.TextInput(
            label="人数",
            placeholder="例：あと2人",
            required=True,
            max_length=100,
        )
        self.party = discord.ui.TextInput(
            label="パーティコード・URL（任意）",
            required=False,
            max_length=300,
        )
        self.comment = discord.ui.TextInput(
            label="ひとこと（任意）",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=500,
        )

        for item in (
            self.modes,
            self.player_count,
            self.party,
            self.comment,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.context.check(interaction):
            return

        try:
            draft = make_draft(
                targets=self.targets,
                modes=self.modes.value,
                player_count=self.player_count.value,
                party=self.party.value,
                comment=self.comment.value,
            )
        except RecruitmentInputError as error:
            await notice(interaction, str(error))
            return

        # ここでは公開投稿せず、確認画面を作る処理へ渡す。
        await self._on_draft(interaction, draft)

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
    ) -> None:
        await report_error(interaction, error, self.context.guild_id)


class RecruitmentTargetView(discord.ui.View):
    def __init__(
        self,
        *,
        context: FormContext,
        on_draft: DraftHandler,
    ) -> None:
        super().__init__(timeout=300)
        self.context = context
        self._on_draft = on_draft

    @discord.ui.select(
        placeholder="通知先を選択してください（両方選択可）",
        min_values=1,
        max_values=2,
        options=[
            discord.SelectOption(label="Gachi", value="gachi"),
            discord.SelectOption(label="Enjoy", value="enjoy"),
        ],
    )
    async def select_targets(
        self,
        interaction: discord.Interaction,
        select: discord.ui.Select,
    ) -> None:
        await self.open_form(interaction, frozenset(select.values))

    async def open_form(
        self,
        interaction: discord.Interaction,
        targets: frozenset[str],
    ) -> None:
        if not await self.context.check(interaction):
            return

        if not targets or not targets <= TARGET_NAMES:
            await notice(interaction, "通知先を選び直してください。")
            return

        modal = RecruitmentModal(
            context=self.context,
            targets=targets,
            on_draft=self._on_draft,
        )
        try:
            await interaction.response.send_modal(modal)
        except BaseException:
            modal.stop()
            raise

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item,
    ) -> None:
        await report_error(interaction, error, self.context.guild_id)
