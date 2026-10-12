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
        line = f"{_name(role.name, 100)}（ID: {role.role_id}）"
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
    embed = discord.Embed(
        title="メンバーの退出記録",
        description="退出の種類は、まだ判別できていません。",
        colour=discord.Colour.orange(),
        timestamp=snapshot.observed_at,
    )
    embed.add_field(
        name="ユーザー名",
        value=_name(snapshot.username),
        inline=False,
    )
    embed.add_field(
        name="表示名",
        value=(
            "取得できませんでした。"
            if snapshot.display_name is None
            else _name(snapshot.display_name)
        ),
        inline=False,
    )
    embed.add_field(
        name="ユーザーID",
        value=str(snapshot.user_id),
        inline=False,
    )
    embed.add_field(
        name="退出時のロール",
        value=_roles_text(snapshot.roles),
        inline=False,
    )
    embed.add_field(
        name="サーバー参加日時",
        value=(
            "取得できませんでした。"
            if snapshot.joined_at is None
            else f"<t:{int(snapshot.joined_at.timestamp())}:F>"
        ),
        inline=False,
    )
    embed.add_field(
        name="退出検知日時",
        value=f"<t:{int(snapshot.observed_at.timestamp())}:F>",
        inline=False,
    )
    # 後の送信処理で、再起動後の重複投稿を防ぐために使う。
    embed.set_footer(text=f"wanbot:leave-log:{snapshot.event_id}")

    if result is not None:
        # 別の人・別のサーバーの照合結果を表示しない。
        encode_result(snapshot, result)
        if result.kind == "unknown":
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
            embed.description = "退出の種類：判別できませんでした。"
            embed.add_field(
                name="判定について",
                value=explanations[result.explanation],
                inline=False,
            )
        else:
            evidence = result.evidence
            embed.description = (
                "退出の種類：キック"
                if result.kind == "kick" else "退出の種類：BAN"
            )
            embed.add_field(
                name="実行者",
                value=_actor_text(evidence),
                inline=False,
            )
            embed.add_field(
                name="理由",
                value=(
                    _name(evidence.reason, 1000)
                    if evidence.reason and evidence.reason.strip()
                    else "理由は未記入、または取得できませんでした。"
                ),
                inline=False,
            )
            embed.add_field(
                name="監査ログID",
                value=str(evidence.entry_id),
                inline=False,
            )
    return embed
