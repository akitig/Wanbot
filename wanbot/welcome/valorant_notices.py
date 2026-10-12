"""診断の状態による操作拒否を、本人向けの案内に変換する。"""

import discord


def diagnosis_notice(error):
    reason = str(error)

    if reason in {
        "回答期限を過ぎています。",
        "Welcome diagnosis has expired",
    }:
        return (
            "この診断は回答期限を過ぎています。"
            "運営にお知らせください。保存済みの回答は残っています。"
        )

    if reason == "この診断は回答済みです。":
        return (
            "この診断は回答済みです。"
            "Welcome内の診断結果をご確認ください。"
        )

    if reason == "現在の質問に回答してください。":
        return (
            "この質問への回答は受け付け済みか、表示が古くなっています。"
            "現在表示されている質問に回答してください。"
            "表示が進んでいない場合は「VALORANT診断を開始・再開する」"
            "から開き直してください。"
        )

    if reason in {
        "古い診断のボタンは使用できません。",
        "この診断は終了しているか、存在しません。",
        "Diagnosis answer source does not match",
    }:
        return (
            "このボタンは現在の診断では使用できません。"
            "Welcomeの「回答を始める・再開する」から"
            "「VALORANT診断を開始・再開する」を押してください。"
        )

    if reason in {
        "Welcome diagnosis is unavailable",
        "Welcome session does not match",
    }:
        return "このWelcomeでは現在、診断を受け付けていません。"

    if reason == "Welcome questionnaire is not eligible":
        return (
            "参加者への質問を完了し、VALORANTを「やる」と回答した方が"
            "この診断を利用できます。"
        )

    if reason == "この診断は本人だけが回答できます。":
        return "この診断は本人だけが回答できます。"

    # 内部の例外文を、そのままDiscordへ出さない。
    return (
        "現在の診断状態では、この操作を受け付けられません。"
        "Welcomeの「回答を始める・再開する」から開き直してください。"
        "解消しない場合は運営にお知らせください。"
    )


async def send_diagnosis_notice(interaction, error):
    options = {
        "ephemeral": True,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    content = diagnosis_notice(error)
    if interaction.response.is_done():
        await interaction.followup.send(content, **options)
    else:
        await interaction.response.send_message(content, **options)
