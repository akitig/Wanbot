"""既存の保存状態を、本人待ちの判定へ変換する。"""

from dataclasses import dataclass, replace
from hashlib import sha256
import json

from wanbot.valorant.session import SessionStatus
from wanbot.welcome.waiting_policy import (
    WaitingFacts,
    decide_waiting,
)


class WaitingStateError(ValueError):
    pass


@dataclass(frozen=True)
class WaitingObservation:
    facts: WaitingFacts
    progress_key: str

    @property
    def decision(self):
        return decide_waiting(self.facts)


def _observation(facts, tokens):
    # 回答本文・氏名・診断の選択内容は含めない。
    encoded = json.dumps(
        tokens, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return WaitingObservation(
        facts=facts,
        progress_key=sha256(encoded).hexdigest(),
    )


class WaitingStateReader:
    def __init__(
        self, *, controller, questions, diagnosis, rules, no_shows
    ):
        self._controller = controller
        self._questions = questions
        self._diagnosis = diagnosis
        self._rules = rules
        self._no_shows = no_shows

    async def _no_show_state(self):
        controller = self._controller
        context = controller._context
        current = []
        completed_rounds = []

        for record in await self._no_shows.all_records():
            if record["session_id"] != context.session_id:
                continue
            if (
                record["guild_id"] != context.guild_id
                or record["user_id"] != context.user_id
                or record["channel_id"] != context.channel_id
            ):
                raise WaitingStateError("不参加記録の対象が一致しません。")

            status = record["status"]
            if status == "reschedule_requested":
                proposal_id = record["proposal_id"]
                archived_request = (
                    await controller._repository.archived_for_reschedule(
                        proposal_id
                    )
                )
                archived_plan = (
                    await controller._meetings.archived_for_reschedule(
                        proposal_id
                    )
                )
                if archived_request is not None and archived_plan is not None:
                    completed_rounds.append(proposal_id)
                    continue
                current.append("rescheduling")
            elif status in (
                "waiting", "second_confirmation", "kick_required"
            ):
                current.append(status)
            else:
                raise WaitingStateError("不参加記録の状態が不正です。")

        if len(current) > 1:
            raise WaitingStateError("進行中の不参加記録が複数あります。")
        return (
            current[0] if current else "none",
            sorted(completed_rounds),
        )

    async def read_locked(self):
        """Welcome共通ロック内から呼ぶ。取得失敗は呼出元へ返す。"""
        controller = self._controller
        context = controller._context
        session = await controller._session()

        admission = await controller._admissions.get(context.session_id)
        kicks = await controller._kick_review._repository.for_session(
            context.session_id
        )

        facts = WaitingFacts(
            session_state=session.state,
            introduction="unknown",
            rules_done=None,
            plays_valorant=None,
            diagnosis_done=None,
            interview_submitted=None,
            meeting="unknown",
            admission_pending=admission is not None,
            kick_pending=any(
                record["status"] in ("executing", "completed")
                or (
                    record["status"] in ("pending", "approved")
                    and "waiting_timeout" not in record
                )
                for record in kicks
            ),
            no_show="unknown",
        )
        tokens = ["waiting-v1"]

        # すでに承認処理中なら、他の入力状況にかかわらず運営待ち。
        if facts.admission_pending or facts.kick_pending:
            return _observation(facts, tokens)

        no_show, rounds = await self._no_show_state()
        facts = replace(facts, no_show=no_show)
        tokens.append(rounds)
        if no_show != "none":
            return _observation(facts, tokens)

        request = await controller.load_request()
        plan = await controller._plan()
        facts = replace(
            facts,
            interview_submitted=request is not None,
            meeting=plan.status if plan is not None else "none",
        )

        # 提出後の日程調整や面談当日までの待機は、本人期限の対象外。
        if request is not None or plan is not None:
            return _observation(facts, tokens)

        snapshot = await self._questions.get(context.session_id)
        if snapshot is None:
            facts = replace(facts, introduction="not_started")
            tokens.append(["question", 0])
            return _observation(facts, tokens)

        if (
            snapshot.session_id != context.session_id
            or snapshot.user_id != context.user_id
            or type(snapshot.revision) is not int
            or snapshot.revision < 0
        ):
            raise WaitingStateError("質問の保存対象または更新番号が不正です。")

        progress = snapshot.progress
        tokens.append(["question", snapshot.revision])
        if progress.status == "age_review_required":
            introduction = "underage"
        elif progress.status == "completed":
            introduction = "completed"
        elif progress.status == "answering":
            introduction = (
                "age_confirmation"
                if progress.next_question == "age_confirmation"
                else "answering"
            )
        else:
            raise WaitingStateError("質問の進行状態が不正です。")

        facts = replace(
            facts,
            introduction=introduction,
            plays_valorant=(
                progress.plays_valo if introduction == "completed" else None
            ),
        )
        if introduction != "completed":
            return _observation(facts, tokens)

        rules_done = await self._rules.has_reacted(context.user_id)
        if type(rules_done) is not bool:
            raise WaitingStateError("ルールの確認結果が不正です。")
        facts = replace(facts, rules_done=rules_done)
        tokens.append(["rules", rules_done])

        if progress.plays_valo is True:
            diagnosis = await self._diagnosis.load_progress()
            if diagnosis is None:
                facts = replace(facts, diagnosis_done=False)
                tokens.append(["diagnosis", None, 0])
            else:
                diagnostic_session = diagnosis.session
                if (
                    diagnostic_session.guild_id != context.guild_id
                    or diagnostic_session.user_id != context.user_id
                ):
                    raise WaitingStateError("診断の保存対象が一致しません。")

                if diagnostic_session.status is SessionStatus.COMPLETED:
                    # 完了状態でも結果が未保存なら、判定は保留する。
                    done = True if diagnosis.result is not None else None
                elif diagnostic_session.status in (
                    SessionStatus.ACTIVE, SessionStatus.EXPIRED
                ):
                    done = False
                else:
                    raise WaitingStateError("診断の進行状態が不正です。")

                facts = replace(facts, diagnosis_done=done)
                tokens.append([
                    "diagnosis",
                    diagnostic_session.session_id,
                    len(diagnostic_session.answers),
                ])
                # 自動的な診断期限切れは、本人の新しい回答として扱わない。
        elif progress.plays_valo is False:
            facts = replace(facts, diagnosis_done=False)
        else:
            raise WaitingStateError("VALORANT参加有無の回答が不正です。")

        return _observation(facts, tokens)
