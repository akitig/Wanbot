"""既存の質問・診断・面談をチェックリストへ接続する。"""

import logging

import discord

from wanbot.logging_config import log_event
from wanbot.valorant.session import SessionStatus
from wanbot.welcome.checklist_panel import ChecklistPanel
from wanbot.welcome.checklist_view import ChecklistState, WelcomeChecklistView
from wanbot.welcome.questionnaire_forms import QuestionnaireContext


def bind_checklist(manager, context: QuestionnaireContext, controller,
                   diagnosis, interview):
    async def build_view():
        snapshot = await manager._questions.get(context.session_id)
        introduction_done = False
        plays_valorant = None

        if snapshot is not None:
            if (
                snapshot.session_id != context.session_id
                or snapshot.user_id != context.user_id
            ):
                raise ValueError("Checklist questionnaire does not match")
            introduction_done = snapshot.progress.status == "completed"
            if introduction_done:
                plays_valorant = snapshot.progress.plays_valo

        diagnosis_done = False
        if introduction_done and plays_valorant is True and diagnosis is not None:
            progress = await diagnosis.load_progress()
            diagnosis_done = (
                progress is not None
                and progress.session.guild_id == context.guild_id
                and progress.session.user_id == context.user_id
                and progress.session.status is SessionStatus.COMPLETED
                and progress.result is not None
            )

        try:
            rules_done = await manager._rules_checker.has_reacted(
                context.user_id
            )
        except discord.HTTPException as error:
            rules_done = None
            log_event(
                "welcome",
                "checklist_rules_lookup_failed",
                guild_id=context.guild_id,
                level=logging.WARNING,
                error=error,
            )

        async def unavailable(interaction):
            await interaction.response.send_message(
                "現在、この手続きは利用できません。",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        request = (
            await interview.load_request()
            if interview is not None
            else None
        )

        meeting = None
        if interview is not None and request is not None:
            from wanbot.welcome.meeting_controller import (
                MeetingInterviewController,
            )
            if isinstance(interview, MeetingInterviewController):
                meeting = await interview.load_meeting()

        return WelcomeChecklistView(
            context=context,
            state=ChecklistState(
                introduction_done=introduction_done,
                rules_done=rules_done,
                plays_valorant=plays_valorant,
                diagnosis_done=diagnosis_done,
                interview_submitted=request is not None,
                meeting_at=(
                    meeting.meeting_at
                    if meeting is not None
                    and meeting.status == "confirmed"
                    else None
                ),
            ),
            rules_url=manager._rules_checker.message_url,
            rules_emoji=manager._rules_emoji,
            on_introduction=controller.begin,
            on_diagnosis=controller.start_diagnosis,
            on_interview=(
                interview.begin if interview is not None else unavailable
            ),
            on_schedule_change=(
                interview._schedule_change.open
                if interview is not None
                and getattr(interview, "_schedule_change", None) is not None
                else None
            ),
        )

    panel = ChecklistPanel(
        client=manager._client,
        context=context,
        repository=manager._panels,
        view=controller.start_view,
        view_factory=build_view,
    )

    async def refresh_after_completion():
        # 呼び出し元がWelcome共通ロックを取得済みなので、
        # manager.ensureは呼ばず、パネルだけ更新する。
        try:
            await panel.ensure(move_to_bottom=True)
        except Exception as error:
            manager._retry.add(context.session_id)
            log_event(
                "welcome",
                "checklist_completion_refresh_failed",
                guild_id=context.guild_id,
                level=logging.ERROR,
                error=error,
            )

    previous_completion = controller._on_completed

    async def introduction_completed(snapshot):
        if previous_completion is not None:
            await previous_completion(snapshot)
        await refresh_after_completion()

    controller._on_completed = introduction_completed
    if diagnosis is not None:
        diagnosis.set_completion_handler(refresh_after_completion)

    from wanbot.welcome.meeting_controller import (
        MeetingInterviewController,
    )
    if isinstance(interview, MeetingInterviewController):
        interview.set_meeting_completion_handler(
            refresh_after_completion
        )

    return panel
