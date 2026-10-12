"""参加承認時のロール決定・付与・確認。"""

from wanbot.valorant.scoring import Category
from wanbot.valorant.session import SessionStatus
from wanbot.welcome.interview_gate import InterviewNotReady


def admission_role_ids(
    guild_config,
    *,
    context,
    questionnaire,
    diagnosis,
):
    if (
        guild_config.guild_id != context.guild_id
        or questionnaire is None
        or questionnaire.session_id != context.session_id
        or questionnaire.user_id != context.user_id
        or questionnaire.progress.status != "completed"
        or questionnaire.progress.adult is not True
    ):
        raise InterviewNotReady("参加者の自己紹介の完了を確認できません。")

    keys = ["MEMBER_ROLE_ID"]
    plays_valo = questionnaire.progress.plays_valo

    if plays_valo is True:
        # 呼び出し元から、今回のWelcome専用の診断を受け取る。
        if (
            diagnosis is None
            or diagnosis.session.guild_id != context.guild_id
            or diagnosis.session.user_id != context.user_id
            or diagnosis.session.status is not SessionStatus.COMPLETED
            or diagnosis.result is None
        ):
            raise InterviewNotReady("今回のVALORANT診断の完了を確認できません。")

        category = diagnosis.result.category
        if category == Category.GACHI:
            keys.append("GACHI_ROLE_ID")
        elif category == Category.ENJOY:
            keys.append("ENJOY_ROLE_ID")
        elif category == Category.NEUTRAL:
            keys.extend(("GACHI_ROLE_ID", "ENJOY_ROLE_ID"))
        else:
            raise InterviewNotReady("診断結果の区分を確認できません。")
    elif plays_valo is not False:
        raise InterviewNotReady("VALORANTをプレイするか確認できません。")

    values = tuple(guild_config.ids.get(key) for key in keys)
    if any(type(value) is not int or not 0 < value < 2**64 for value in values):
        raise InterviewNotReady("参加承認用のロールIDが設定されていません。")
    if len(set(values)) != len(values):
        raise InterviewNotReady("構成員・診断ロールに同じIDが設定されています。")

    return values


async def grant_admission_roles(
    *,
    client,
    guild_id,
    user_id,
    role_ids,
):
    role_ids = tuple(role_ids)
    if (
        not role_ids
        or any(
            type(value) is not int or not 0 < value < 2**64
            for value in role_ids
        )
        or len(set(role_ids)) != len(role_ids)
    ):
        raise InterviewNotReady("付与するロールの指定が不正です。")

    guild = client.get_guild(guild_id)
    if (
        guild is None
        or guild.id != guild_id
        or guild.unavailable
        or client.user is None
    ):
        raise InterviewNotReady("サーバーを確認できません。")

    bot = await guild.fetch_member(client.user.id)
    member = await guild.fetch_member(user_id)
    if (
        bot.guild.id != guild_id
        or member.guild.id != guild_id
        or member.id != user_id
        or member.bot
    ):
        raise InterviewNotReady("参加者の所属を確認できません。")
    if not bot.guild_permissions.manage_roles:
        raise InterviewNotReady("Botにロール管理権限がありません。")

    roles = {role.id: role for role in await guild.fetch_roles()}
    bot_role_ids = {role.id for role in bot.roles}
    bot_roles = [role for role in roles.values() if role.id in bot_role_ids]
    if not bot_roles:
        raise InterviewNotReady("Botのロール順位を確認できません。")
    bot_top = max(bot_roles)

    selected = []
    for role_id in role_ids:
        role = roles.get(role_id)
        if (
            role is None
            or role.guild.id != guild_id
            or role.is_default()
            or role.managed
            or not bot_top > role
        ):
            raise InterviewNotReady(
                "付与できないロールがあります。"
                "設定IDとBotのロール順位を確認してください。"
            )
        selected.append(role)

    existing = {role.id for role in member.roles}
    missing = [role for role in selected if role.id not in existing]
    if missing:
        await member.add_roles(
            *missing,
            reason="Wanbot interview approval",
            atomic=True,
        )

    checked = await guild.fetch_member(user_id)
    actual = {role.id for role in checked.roles}
    if (
        checked.id != user_id
        or checked.guild.id != guild_id
        or not set(role_ids).issubset(actual)
    ):
        raise InterviewNotReady(
            "必要なロールの付与を確認できませんでした。"
            "参加手続きはまだ終了していません。"
        )

    return checked
