"""退出付近のキック・BAN監査ログを取得する。"""

from dataclasses import dataclass
from datetime import datetime, timedelta

import discord

from wanbot.leave_log.audit_match import AuditEvidence, MATCH_WINDOW
from wanbot.leave_log.models import DepartureDataError


SETTLE_DELAY = timedelta(seconds=45)
MAX_ENTRIES_PER_ACTION = 200


@dataclass(frozen=True)
class AuditLookup:
    entries: tuple[AuditEvidence, ...]
    complete: bool
    status: str


async def _actor_names(guild, entry, actor_id):
    if actor_id is None:
        return None, None

    actor = getattr(entry, "user", None)
    if getattr(actor, "id", None) != actor_id:
        actor = None

    # サーバー内のニックネームを優先する。
    get_member = getattr(guild, "get_member", None)
    member = get_member(actor_id) if callable(get_member) else None
    if member is None and isinstance(actor, discord.Member):
        member = actor

    if member is None:
        fetch_member = getattr(guild, "fetch_member", None)
        if callable(fetch_member):
            try:
                member = await fetch_member(actor_id)
            except discord.HTTPException:
                # 退会済み・取得不可でも、監査ログの照合は続ける。
                member = None

    if (
        member is not None
        and getattr(member, "id", None) == actor_id
        and getattr(getattr(member, "guild", None), "id", None) == guild.id
    ):
        actor = member

    if actor is None:
        return None, None

    username = getattr(actor, "name", None)
    display_name = getattr(actor, "display_name", None)
    if type(username) is not str or not username.strip():
        username = None
    if type(display_name) is not str or not display_name.strip():
        display_name = username
    return username, display_name


async def read_departure_audit(guild, snapshot, *, now):
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise DepartureDataError("現在日時が不正です。")
    if guild is None or guild.id != snapshot.guild_id:
        raise DepartureDataError("監査ログ取得先のサーバーが一致しません。")
    if guild.unavailable:
        return AuditLookup((), False, "guild_unavailable")
    if now < snapshot.observed_at + SETTLE_DELAY:
        return AuditLookup((), False, "waiting")

    # 日時境界の取りこぼしを避け、厳密な時間範囲は照合側で判定する。
    after = snapshot.observed_at - MATCH_WINDOW - timedelta(seconds=1)
    before = snapshot.observed_at + MATCH_WINDOW + timedelta(seconds=1)
    found = []

    try:
        for action, name in (
            (discord.AuditLogAction.kick, "kick"),
            (discord.AuditLogAction.ban, "ban"),
        ):
            count = 0
            async for entry in guild.audit_logs(
                limit=MAX_ENTRIES_PER_ACTION + 1,
                action=action,
                after=after,
                before=before,
                oldest_first=True,
            ):
                count += 1
                if count > MAX_ENTRIES_PER_ACTION:
                    return AuditLookup((), False, "limit_reached")
                if entry.guild.id != snapshot.guild_id or entry.action != action:
                    raise DepartureDataError("監査ログの所属または操作が不正です。")

                target_id = getattr(entry.target, "id", None)
                if type(target_id) is not int:
                    raise DepartureDataError("監査ログの対象者を確認できません。")
                if target_id != snapshot.user_id:
                    continue

                actor_id = getattr(entry, "user_id", None)
                if actor_id is None:
                    actor_id = getattr(entry.user, "id", None)
                actor_username, actor_display_name = await _actor_names(
                    guild, entry, actor_id
                )
                found.append(AuditEvidence(
                    entry_id=entry.id,
                    guild_id=entry.guild.id,
                    target_id=target_id,
                    action=name,
                    created_at=entry.created_at,
                    actor_id=actor_id,
                    reason=entry.reason,
                    actor_username=actor_username,
                    actor_display_name=actor_display_name,
                ))
    except discord.Forbidden:
        return AuditLookup((), False, "forbidden")
    except discord.HTTPException:
        return AuditLookup((), False, "http_error")
    except DepartureDataError:
        return AuditLookup((), False, "invalid_audit_data")

    return AuditLookup(tuple(found), True, "fetched")
