from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import discord

from wanbot.logging_config import log_event
from wanbot.recruitment.forms import FormContext, notice, report_error
from wanbot.recruitment.models import RecruitmentDraft
from wanbot.recruitment.presentation import build_post


PublishHandler = Callable[
    [discord.Interaction, RecruitmentDraft],
    Awaitable[None],
]


class RecruitmentConfirmView(discord.ui.View):
    def __init__(
        self,
        *,
        context: FormContext,
        draft: RecruitmentDraft,
        on_publish: PublishHandler,
    ) -> None:
        super().__init__(timeout=300)
        self.context = context
        self.draft = draft
        self._on_publish = on_publish
        self._state = "ready"

    @discord.ui.button(
        label="完了",
        style=discord.ButtonStyle.success,
    )
    async def complete(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not await self.context.check(interaction):
            return

        if self._state != "ready":
            await notice(
                interaction,
                "この確認画面は処理中、または操作済みです。",
            )
            return

        # awaitの前に状態を変え、同じ画面からの連打を防ぐ。
        self._state = "publishing"

        try:
            await interaction.response.defer()
        except BaseException:
            # 投稿処理を呼ぶ前なので、再操作できる状態へ戻す。
            self._state = "ready"
            raise

        try:
            await self._on_publish(interaction, self.draft)
        except Exception as error:
            self._state = "failed"
            log_event(
                "recruitment",
                "publish_failed",
                guild_id=self.context.guild_id,
                level=logging.ERROR,
                error=error,
            )
            content = (
                "投稿の完了を確認できませんでした。\n"
                "募集チャンネルに投稿されていないか確認してください。"
                "重複を防ぐため、自動再送は行いません。"
            )
        else:
            self._state = "published"
            content = "募集を投稿しました。"
        finally:
            self.stop()

        await interaction.edit_original_response(
            content=content,
            embed=None,
            view=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(
        label="キャンセル",
        style=discord.ButtonStyle.secondary,
    )
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not await self.context.check(interaction):
            return

        if self._state != "ready":
            await notice(
                interaction,
                "この確認画面は処理中、または操作済みです。",
            )
            return

        self._state = "cancelled"
        self.stop()

        await interaction.response.edit_message(
            content="募集をキャンセルしました。",
            embed=None,
            view=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item,
    ) -> None:
        await report_error(interaction, error, self.context.guild_id)


async def send_confirmation(
    interaction: discord.Interaction,
    draft: RecruitmentDraft,
    *,
    context: FormContext,
    gachi_role_id: int,
    enjoy_role_id: int,
    on_publish: PublishHandler,
) -> RecruitmentConfirmView | None:
    if not await context.check(interaction):
        return None

    post = build_post(
        draft,
        author_id=context.user_id,
        gachi_role_id=gachi_role_id,
        enjoy_role_id=enjoy_role_id,
    )
    post.embed.title = "VALORANT募集の確認"
    post.embed.set_footer(
        text="「完了」で投稿します。確認画面の有効時間は5分です。"
    )

    view = RecruitmentConfirmView(
        context=context,
        draft=draft,
        on_publish=on_publish,
    )

    try:
        await interaction.response.send_message(
            content="まだ投稿されていません。内容を確認してください。",
            embed=post.embed,
            view=view,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except BaseException:
        view.stop()
        raise

    return view
