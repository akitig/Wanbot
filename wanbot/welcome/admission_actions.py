"""面談完了ボタンと、管理者向けの参加承認確認。"""

from wanbot.welcome.admission_roles import admission_role_ids
from wanbot.welcome.interview_gate import InterviewNotReady
from wanbot.welcome.meeting_views import MeetingView
from wanbot.welcome.kick_review import rejection_button


class AdmissionActions:
    def admission_view(self, proposal_id):
        view = MeetingView(authorize=self._authorize, timeout=None)

        async def open_confirmation(interaction):
            async def operation(actor):
                plan = await self._plan()
                if (
                    plan is None
                    or plan.status != "confirmed"
                    or plan.proposal_id != proposal_id
                ):
                    raise InterviewNotReady("現在の確定済み面談ではありません。")

                confirmation = MeetingView(
                    authorize=self._owner_authorizer(actor.id),
                    timeout=600,
                )

                async def approve(current):
                    await self.approve_admission(
                        current, proposal_id, actor.id
                    )

                async def cancel(current):
                    await self._notice(
                        current,
                        "承認せずに閉じました。参加手続きは継続中です。",
                    )

                confirmation.add_action(
                    label="参加を承認する",
                    custom_id=f"wanbot:admission:approve:{proposal_id}",
                    action=approve,
                )
                confirmation.add_action(
                    label="承認せずに閉じる",
                    custom_id=f"wanbot:admission:cancel:{proposal_id}",
                    action=cancel,
                )
                confirmation.add_item(
                    rejection_button(self, proposal_id, actor.id)
                )
                self._views.append(confirmation)
                await self._notice(
                    interaction,
                    "面談を完了し、参加を承認しますか？\n\n"
                    "承認すると、構成員ロールと該当する診断ロールを付与し、"
                    "Welcomeを運営専用のLogへ移動します。",
                    confirmation,
                )

            await self._operate(interaction, operation)

        view.add_action(
            label="面談完了",
            custom_id=f"wanbot:admission:finish:{proposal_id}",
            action=open_confirmation,
        )
        binding = getattr(self, "_no_show", None)
        if binding is not None:
            binding.add_report_button(view, proposal_id)
        return view

    async def approve_admission(self, interaction, proposal_id, actor_id):
        async def operation(actor):
            plan = await self._plan()
            if (
                plan is None
                or plan.status != "confirmed"
                or plan.proposal_id != proposal_id
            ):
                raise InterviewNotReady("現在の確定済み面談ではありません。")

            binding = getattr(self, "_no_show", None)
            if binding is not None:
                await binding.guard_admission(proposal_id)

            kick_requests = await self._kick_review._repository.for_session(
                self._context.session_id
            )
            if any(
                record["status"] in (
                    "pending", "approved", "executing", "completed"
                )
                for record in kick_requests
            ):
                raise InterviewNotReady(
                    "キック確認依頼があるため、参加承認はできません。"
                )

            questions = await self._admission_questions.get(
                self._context.session_id
            )
            diagnosis = None
            if (
                questions is not None
                and questions.progress.plays_valo is True
            ):
                diagnosis = await self._admission_diagnosis()

            config = self._meeting_config.guild_for(self._context.guild_id)
            if config is None:
                raise InterviewNotReady("サーバー設定が見つかりません。")

            roles = admission_role_ids(
                config,
                context=self._context,
                questionnaire=questions,
                diagnosis=diagnosis,
            )

            await self._session()
            record = await self._admissions.begin(
                context=self._context,
                plan=plan,
                actor_id=actor.id,
                role_ids=roles,
            )

            await self._admission_service.finish_locked(record)
            await self._notice(
                interaction,
                "参加を承認しました。\n"
                "必要なロールの付与と、WelcomeのLog移動が完了しました。",
            )

        await self._operate(interaction, operation, actor_id)
