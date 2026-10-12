"""回答一覧にルールへのリンクと確認ボタンを追加する。"""

import discord


RULES_GUIDANCE = (
    "ルールをご確認の上、指定のリアクションをお願いします。\n"
    "リアクション後に「リアクションを押しました」を押してください。\n"
    "ルールにリアクションがないと、先に進めません。"
)


def add_rules_buttons(view, *, context, checker, on_check):
    view.add_item(
        discord.ui.Button(
            label="ルールを開く",
            style=discord.ButtonStyle.link,
            url=checker.message_url,
            row=2,
        )
    )
    button = discord.ui.Button(
        label="リアクションを押しました",
        style=discord.ButtonStyle.primary,
        row=2,
    )

    async def callback(interaction):
        if not await context.check(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await on_check(interaction)

    button.callback = callback
    view.add_item(button)
