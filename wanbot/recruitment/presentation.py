from __future__ import annotations

from dataclasses import dataclass

import discord

from wanbot.recruitment.models import RecruitmentDraft, make_draft


@dataclass(frozen=True)
class RecruitmentPost:
    content: str
    embed: discord.Embed
    allowed_mentions: discord.AllowedMentions


def _check_id(value: int) -> None:
    if type(value) is not int or not 0 < value < 1 << 64:
        raise ValueError("Invalid Discord ID")


def build_post(
    draft: RecruitmentDraft,
    *,
    author_id: int,
    gachi_role_id: int,
    enjoy_role_id: int,
) -> RecruitmentPost:
    _check_id(author_id)
    _check_id(gachi_role_id)
    _check_id(enjoy_role_id)

    if gachi_role_id == enjoy_role_id:
        raise ValueError("Recruitment roles must be different")

    # 直接作成されたDraftも、投稿前に改めて検証する。
    draft = make_draft(
        targets=draft.targets,
        modes=draft.modes,
        player_count=draft.player_count,
        party=draft.party,
        comment=draft.comment,
    )

    role_ids = []
    labels = []

    if "gachi" in draft.targets:
        role_ids.append(gachi_role_id)
        labels.append("Gachi")

    if "enjoy" in draft.targets:
        role_ids.append(enjoy_role_id)
        labels.append("Enjoy")

    embed = discord.Embed(
        title="VALORANT募集",
        colour=discord.Colour.red(),
    )
    embed.add_field(
        name="募集者",
        value=f"<@{author_id}>",
        inline=False,
    )
    embed.add_field(
        name="通知先",
        value=" / ".join(labels),
        inline=False,
    )
    embed.add_field(
        name="募集モード",
        value=draft.modes,
        inline=False,
    )
    embed.add_field(
        name="人数",
        value=draft.player_count,
        inline=False,
    )

    if draft.party:
        embed.add_field(
            name="パーティコード・URL",
            value=draft.party,
            inline=False,
        )

    if draft.comment:
        embed.add_field(
            name="ひとこと",
            value=draft.comment,
            inline=False,
        )

    return RecruitmentPost(
        content=" ".join(f"<@&{role_id}>" for role_id in role_ids),
        embed=embed,
        allowed_mentions=discord.AllowedMentions(
            everyone=False,
            users=False,
            roles=[discord.Object(id=role_id) for role_id in role_ids],
            replied_user=False,
        ),
    )
