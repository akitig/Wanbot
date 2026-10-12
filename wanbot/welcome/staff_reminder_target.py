"""保存済みの状態から、運営への催促対象を決める。"""

from dataclasses import dataclass
from hashlib import sha256


class StaffReminderTargetError(ValueError):
    pass


@dataclass(frozen=True)
class StaffReminderTarget:
    key: str
    kind: str
    session_id: str
    welcome_channel_id: int


def select_staff_target(
    *,
    context,
    request,
    plan,
    kick_reviews,
    active=True,
    admission_pending=False,
    no_show_pending=False,
):
    """送信先の取得やDiscordへの送信は行わない。"""
    if not active or admission_pending:
        return None

    pending = []
    for record in kick_reviews:
        if (
            record["guild_id"] != context.guild_id
            or record["session_id"] != context.session_id
            or record["user_id"] != context.user_id
            or record["channel_id"] != context.channel_id
        ):
            raise StaffReminderTargetError("キック確認の対象が一致しません。")

        if record["status"] in ("executing", "completed"):
            return None
        if record["status"] in ("pending", "approved"):
            pending.append(record)
        elif record["status"] not in ("cancelled", "obsolete"):
            raise StaffReminderTargetError("キック確認の状態が不明です。")

    if len(pending) > 1:
        raise StaffReminderTargetError("進行中のキック確認が複数あります。")

    if pending:
        return StaffReminderTarget(
            key=f"kick:{pending[0]['request_id']}",
            kind="kick_review",
            session_id=context.session_id,
            welcome_channel_id=context.channel_id,
        )

    # 不参加後の本人回答待ち・再調整処理中は日程選択を催促しない。
    if no_show_pending:
        return None

    if request is None:
        if plan is not None:
            raise StaffReminderTargetError("面談希望日時を確認できません。")
        return None

    if (
        request.session_id != context.session_id
        or request.user_id != context.user_id
        or request.channel_id != context.channel_id
    ):
        raise StaffReminderTargetError("面談希望の対象が一致しません。")

    if plan is not None:
        if (
            plan.welcome_session_id != context.session_id
            or plan.guild_id != context.guild_id
            or plan.user_id != context.user_id
            or plan.channel_id != context.channel_id
        ):
            raise StaffReminderTargetError("面談の対象が一致しません。")

        if plan.status == "confirmed":
            return None
        if plan.status == "waiting_second":
            kind = "second_manager"
        elif plan.status == "declined":
            kind = "date_selection"
        else:
            raise StaffReminderTargetError("面談の状態が不明です。")
        suffix = f"{plan.status}:{plan.proposal_id}"
    else:
        kind = "date_selection"
        suffix = "initial"

    # 同じWelcomeで再提出しても、以前の催促時刻を引き継がない。
    submitted = request.confirmed_at
    if submitted.utcoffset() is None:
        raise StaffReminderTargetError("面談希望の提出日時が不正です。")

    identity = (
        f"{context.session_id}:{submitted.isoformat()}:{suffix}"
    )
    digest = sha256(identity.encode("utf-8")).hexdigest()
    return StaffReminderTarget(
        key=f"meeting:{digest}",
        kind=kind,
        session_id=context.session_id,
        welcome_channel_id=context.channel_id,
    )
