"""Welcomeの進捗と、各手続きへのボタンを縦に表示する。"""

import logging
from dataclasses import dataclass
from datetime import datetime

import discord

from wanbot.logging_config import log_event
from wanbot.welcome.interview_dates import format_date


@dataclass(frozen=True)
class ChecklistState:
    introduction_done: bool = False
    rules_done: bool | None = False
    plays_valorant: bool | None = None
    diagnosis_done: bool = False
    interview_submitted: bool = False
    meeting_at: datetime | None = None
    meeting_completed: bool = False

    @property
    def interview_ready(self):
        diagnosis_ready = (
            self.plays_valorant is False
            or (
                self.plays_valorant is True
                and self.diagnosis_done
            )
        )
        return (
            self.introduction_done
            and self.rules_done is True
            and diagnosis_ready
        )


class WelcomeChecklistView(discord.ui.LayoutView):
    def __init__(
        self,
        *,
        context,
        state,
        rules_url,
        rules_emoji,
        on_introduction,
        on_diagnosis,
        on_interview,
        on_schedule_change=None,
    ):
        super().__init__(timeout=None)
        self._context = context

        self.add_item(discord.ui.TextDisplay("## 参加手続き"))

        self._heading(
            state.introduction_done,
            "自己紹介を入力する",
        )
        self._action(
            label=(
                "確認・修正する"
                if state.introduction_done
                else "はじめる"
            ),
            custom_id=f"wanbot:welcome:questions:{context.session_id}",
            handler=on_introduction,
            defer=True,
        )

        rules_text = f"ルールを読んで{rules_emoji}をする"
        if state.rules_done is None:
            rules_text += "\n現在、リアクションの状態を確認できません。"
        self._heading(state.rules_done is True, rules_text)
        self.add_item(
            discord.ui.ActionRow(
                discord.ui.Button(
                    label="ルールはこちら",
                    style=discord.ButtonStyle.link,
                    url=rules_url,
                )
            )
        )

        if state.plays_valorant is False:
            self.add_item(
                discord.ui.TextDisplay(
                    "VALORANT診断：対象外（「やらない」と回答済み）"
                )
            )
        else:
            self._heading(
                state.diagnosis_done,
                "VALORANT診断をする",
            )
            self._action(
                label="結果を見る" if state.diagnosis_done else "はじめる",
                custom_id=f"wanbot:welcome:diagnosis:{context.session_id}",
                handler=on_diagnosis,
                defer=True,
                disabled=(
                    not state.introduction_done
                    or state.plays_valorant is not True
                ),
            )
            if not state.introduction_done:
                self.add_item(
                    discord.ui.TextDisplay(
                        "先に自己紹介を入力してください。"
                    )
                )

        self._heading(
            state.interview_submitted,
            "面談の希望日時を入力する",
        )
        self._action(
            label="提出内容を見る" if state.interview_submitted else "はじめる",
            custom_id=f"wanbot:welcome:interview:{context.session_id}",
            handler=on_interview,
            defer=False,
            disabled=not (
                state.interview_submitted or state.interview_ready
            ),
        )

        if on_schedule_change is not None and state.interview_submitted:
            change_button = discord.ui.Button(
                label="日程変更・再提出",
                style=discord.ButtonStyle.secondary,
                custom_id=f"wanbot:welcome:schedule-change:{context.session_id}",
            )
            change_button.callback = on_schedule_change
            self.add_item(discord.ui.ActionRow(change_button))

        self._heading(state.meeting_completed, "面談に参加する")
        self.add_item(
            discord.ui.TextDisplay(
                format_date(state.meeting_at)
                if state.meeting_at is not None
                else "未定"
            )
        )

    def _heading(self, done, text):
        mark = "✅" if done else "□"
        self.add_item(discord.ui.TextDisplay(f"**{mark} {text}**"))

    def _action(
        self,
        *,
        label,
        custom_id,
        handler,
        defer,
        disabled=False,
    ):
        button = discord.ui.Button(
            label=label,
            style=discord.ButtonStyle.primary,
            custom_id=custom_id,
            disabled=disabled,
        )

        async def callback(interaction):
            if not await self._context.check(interaction):
                return
            if disabled:
                return
            if defer:
                # 公開チェックリストではなく、本人用の画面を開く。
                await interaction.response.defer(
                    ephemeral=True, thinking=True
                )
            await handler(interaction)

        button.callback = callback
        self.add_item(discord.ui.ActionRow(button))

    async def on_error(self, interaction, error, item):
        log_event(
            "welcome",
            "checklist_interaction_failed",
            guild_id=self._context.guild_id,
            level=logging.ERROR,
            error=error,
        )
        content = (
            "手続きを開けませんでした。"
            "少し待ってから、もう一度ボタンを押してください。"
        )
        options = {
            "ephemeral": True,
            "allowed_mentions": discord.AllowedMentions.none(),
        }
        if interaction.response.is_done():
            await interaction.followup.send(content, **options)
        else:
            await interaction.response.send_message(content, **options)
