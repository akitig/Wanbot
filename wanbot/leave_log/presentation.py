"""保存済みの退出情報を、Discordの記録用表示へ変換する。"""

import discord

from wanbot.leave_log.models import DepartureSnapshot
from wanbot.leave_log.result_repository import encode_result


def _name(value, limit=200):
    # 名前をMarkdownの装飾やメンションとして表示しない。
    value = " ".join(value.split())
    value = discord.utils.escape_mentions(value)
    value = discord.utils.escape_markdown(value)
    if len(value) > limit:
        value = value[:limit - 1] + "…"
    return value


def _actor_text(evidence):
    username = evidence.actor_username
    display_name = evidence.actor_display_name
    if username is not None:
        return (
            f"{_name(display_name or username)}"
            f"（{_name(username)}）"
        )
    if display_name is not None:
        return _name(display_name)
    if evidence.actor_id is not None:
        return f"取得できませんでした（ID: {evidence.actor_id}）"
    return "取得できませんでした。"


def _roles_text(roles):
    if roles is None:
        return "取得できませんでした。"
    if not roles:
        return "記録対象のロールはありません。"

    lines = []
    for index, role in enumerate(roles):
        line = f"<@&{role.role_id}>"
        remaining = len(roles) - index - 1
        suffix = f"\nほか {remaining} 件（保存データに記録済み）"
        candidate = "\n".join((*lines, line))
        reserved = len(suffix) if remaining else 0
        if len(candidate) + reserved > 1024:
            omitted = len(roles) - index
            lines.append(f"ほか {omitted} 件（保存データに記録済み）")
            break
        lines.append(line)
    return "\n".join(lines)


def departure_embed(snapshot: DepartureSnapshot, result=None):
    if result is not None:
        encode_result(snapshot, result)

    kind = result.kind if result is not None else "unknown"
    title, colour = {
        "kick": ("🦶 ユーザーが追放されました", 0xF5CE58),
        "ban": ("🕊️ ユーザーがBANされました", 0x747BFF),
        "unknown": ("🚪 ユーザーが退出しました", 0x95A5A6),
    }[kind]
    embed = discord.Embed(title=title, colour=colour)

    if snapshot.avatar_url is not None:
        embed.set_thumbnail(url=snapshot.avatar_url)

    display_name = snapshot.display_name or snapshot.username
    embed.add_field(
        name="👤 ユーザー",
        value=(
            f"<@{snapshot.user_id}>\n"
            f"{_name(display_name)}（{_name(snapshot.username)}）"
        ),
        inline=False,
    )
    embed.add_field(
        name="🆔 ID",
        value=f"`{snapshot.user_id}`",
        inline=False,
    )
    embed.add_field(
        name="🎭 退出時ロール",
        value=_roles_text(snapshot.roles),
        inline=False,
    )

    evidence = result.evidence if result is not None else None
    if evidence is None:
        reason = "退出理由は判別できませんでした。"
        actor = "特定できませんでした。"
    else:
        reason = (
            _name(evidence.reason, 1000)
            if evidence.reason and evidence.reason.strip()
            else "理由は未記入、または取得できませんでした。"
        )
        actor = _actor_text(evidence)

    embed.add_field(name="📝 理由", value=reason, inline=False)
    embed.add_field(name="👮 実行者", value=actor, inline=False)
    embed.add_field(
        name="📥 サーバー参加日時",
        value=(
            "取得できませんでした。"
            if snapshot.joined_at is None
            else f"<t:{int(snapshot.joined_at.timestamp())}:F>"
        ),
        inline=False,
    )
    embed.add_field(
        name="📤 退出検知日時",
        value=f"<t:{int(snapshot.observed_at.timestamp())}:F>",
        inline=False,
    )

    if kind == "unknown":
        explanations = {
            "audit_unavailable_or_incomplete": (
                "監査ログを取得できなかった、または"
                "必要な範囲の取得を完了できませんでした。"
            ),
            "conflicting_audit_data": (
                "取得した監査ログの内容が一致しませんでした。"
            ),
            "no_matching_audit": (
                "照合範囲内に、対象者のキック・BAN記録は"
                "見つかりませんでした。通常退出の可能性がありますが、"
                "断定はできません。"
            ),
            "multiple_matching_audits": (
                "一致する監査ログが複数あり、特定できませんでした。"
            ),
        }
        explanation = (
            "退出の種類は、まだ判別できていません。"
            if result is None else explanations[result.explanation]
        )
        embed.add_field(
            name="ℹ️ 判定について", value=explanation, inline=False
        )

    # 再起動後の重複投稿防止に使う識別子は維持する。
    embed.set_footer(text=f"wanbot:leave-log:{snapshot.event_id}")
    return embed
