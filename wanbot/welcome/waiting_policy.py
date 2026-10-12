"""本人待ちの期限を適用する状態を判定する。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class WaitingFacts:
    # Discordや保存データから読み取った状態を、接続側で整理して渡す。
    session_state: str
    introduction: str
    rules_done: bool | None
    plays_valorant: bool | None
    diagnosis_done: bool | None
    interview_submitted: bool | None
    meeting: str
    admission_pending: bool | None
    kick_pending: bool | None
    no_show: str


@dataclass(frozen=True)
class WaitingDecision:
    owner: str
    reason: str

    @property
    def counts_applicant_time(self):
        return self.owner == "applicant"


def _validate(facts):
    choices = {
        "session_state": {
            "preparing", "active", "archiving", "closed", "unknown",
        },
        "introduction": {
            "not_started", "answering", "age_confirmation",
            "underage", "completed", "unknown",
        },
        "meeting": {
            "none", "waiting_second", "confirmed", "declined", "unknown",
        },
        "no_show": {
            "none", "waiting", "second_confirmation",
            "kick_required", "rescheduling", "unknown",
        },
    }
    for name, allowed in choices.items():
        value = getattr(facts, name)
        if type(value) is not str or value not in allowed:
            raise ValueError(f"Invalid waiting state: {name}")

    for name in (
        "rules_done", "plays_valorant", "diagnosis_done",
        "interview_submitted", "admission_pending", "kick_pending",
    ):
        value = getattr(facts, name)
        if value is not None and type(value) is not bool:
            raise ValueError(f"Invalid waiting flag: {name}")


def decide_waiting(facts):
    _validate(facts)

    if facts.session_state != "active":
        return WaitingDecision("excluded", "session_not_active")

    if facts.admission_pending is True:
        return WaitingDecision("staff", "admission")
    if facts.kick_pending is True:
        return WaitingDecision("staff", "kick_review")
    if facts.admission_pending is None or facts.kick_pending is None:
        return WaitingDecision("unknown", "review_status_unavailable")

    if facts.no_show == "unknown":
        return WaitingDecision("unknown", "no_show_status_unavailable")
    if facts.no_show == "waiting":
        return WaitingDecision("excluded", "no_show_one_hour")
    if facts.no_show in ("second_confirmation", "kick_required"):
        return WaitingDecision("staff", "no_show_review")
    if facts.no_show == "rescheduling":
        return WaitingDecision("excluded", "reschedule_in_progress")

    # 日程提出後は、以前の質問を変更した場合なども本人期限に戻さない。
    if facts.meeting == "unknown":
        return WaitingDecision("unknown", "meeting_status_unavailable")
    if facts.meeting == "confirmed":
        return WaitingDecision("scheduled", "meeting_confirmed")
    if facts.meeting in ("waiting_second", "declined"):
        return WaitingDecision("staff", "meeting_coordination")
    if facts.interview_submitted is True:
        return WaitingDecision("staff", "meeting_selection")
    if facts.interview_submitted is None:
        return WaitingDecision("unknown", "interview_status_unavailable")

    if facts.introduction == "unknown":
        return WaitingDecision("unknown", "introduction_status_unavailable")
    if facts.introduction == "underage":
        return WaitingDecision("staff", "age_review")
    if facts.introduction in (
        "not_started", "answering", "age_confirmation"
    ):
        return WaitingDecision("applicant", "introduction")

    if facts.rules_done is None:
        return WaitingDecision("unknown", "rules_status_unavailable")
    if facts.rules_done is False:
        return WaitingDecision("applicant", "rules")

    if facts.plays_valorant is None:
        return WaitingDecision("unknown", "valorant_answer_unavailable")
    if facts.plays_valorant is True:
        if facts.diagnosis_done is None:
            return WaitingDecision("unknown", "diagnosis_status_unavailable")
        if facts.diagnosis_done is False:
            return WaitingDecision("applicant", "diagnosis")

    return WaitingDecision("applicant", "interview_preferences")
