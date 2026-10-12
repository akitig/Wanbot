"""面談不参加の本人回答と、管理者の最終確認画面。"""

import logging
from datetime import datetime

import discord

from wanbot.logging_config import log_event


async def _notice(interaction, content):
    await interaction.edit_original_response(
        content=content,
        embed=None,
        view=None,
        allowed_mentions=discord.AllowedMentions.none(),
    )


def no_show_message(record):
    """期限は保存済みの日時を表示し、再表示で延長しない。"""
    if record["status"] == "waiting":
        deadline = datetime.fromisoformat(record["deadline"])
        timestamp = int(deadline.timestamp())
        return (
            f'<@{record["user_id"]}> 面談への参加を確認できませんでした。\n'
            "日程を再調整しますか？\n\n"
            f"回答期限：<t:{timestamp}:F>（<t:{timestamp}:R>）\n"
            "「再調整する」を選ぶと、希望日時を入力し直せます。\n"
            "不参加による再調整は1回のみです。\n\n"
            "「再調整しない」を選んだ場合、または期限までに回答がない場合は、"
            "管理者へキックの承認を依頼します。"
        )

    if record["status"] == "second_confirmation":
        return (
            "再調整した面談でも不参加となっています。\n"
            "不参加による再調整は1回までのため、"
            "管理者専用チャンネルへキック承認を依頼しますか？\n\n"
            "この確認だけではキックしません。"
            "専用チャンネルでの承認後に実行します。"
        )

    raise ValueError("この状態には不参加確認画面を表示できません。")


class NoShowView(discord.ui.View):
    def __init__(
        self,
        *,
        record,
        authorize,
        on_answer=None,
        on_confirm=None,
    ):
        super().__init__(timeout=None)
        self._record = dict(record)
        self._authorize = authorize
        self._on_answer = on_answer
        self._on_confirm = on_confirm

        if record["status"] == "waiting":
            if on_answer is None:
                raise ValueError("本人回答の処理が必要です。")
            self._add_answer("再調整する", True)
            self._add_answer("再調整しない", False)
        elif record["status"] == "second_confirmation":
            if on_confirm is None:
                raise ValueError("管理者確認の処理が必要です。")
            button = discord.ui.Button(
                label="キック承認依頼へ進む",
                style=discord.ButtonStyle.danger,
                custom_id=(
                    f'wanbot:no-show:confirm:{record["proposal_id"]}'
                ),
            )
            button.callback = self._confirm
            self.add_item(button)
        else:
            raise ValueError("この状態にはボタンを表示できません。")

    async def _check(self, interaction, *, applicant):
        await interaction.response.defer(ephemeral=True, thinking=True)
        record = self._record

        if (
            interaction.guild_id != record["guild_id"]
            or interaction.channel_id != record["channel_id"]
        ):
            await _notice(
                interaction, "対象のWelcomeチャンネルで操作してください。"
            )
            return False

        if applicant:
            if interaction.user.id != record["user_id"]:
                await _notice(interaction, "この回答は本人だけが操作できます。")
                return False
        elif interaction.user.id == record["user_id"]:
            await _notice(
                interaction,
                "管理者以外押せません、対応まで少々お待ちください。",
            )
            return False

        # 接続先で最新の在籍・権限・保存状態を確認する。
        # Falseを返す場合、接続先が理由を本人だけに表示する。
        return await self._authorize(interaction)

    def _add_answer(self, label, reschedule):
        action = "yes" if reschedule else "no"
        button = discord.ui.Button(
            label=label,
            style=(
                discord.ButtonStyle.primary
                if reschedule else discord.ButtonStyle.secondary
            ),
            custom_id=(
                f'wanbot:no-show:{action}:{self._record["proposal_id"]}'
            ),
        )

        async def callback(interaction):
            if not await self._check(interaction, applicant=True):
                return
            # 接続先で共通ロックを取得し、期限と状態を再確認して保存する。
            await self._on_answer(
                interaction,
                self._record["proposal_id"],
                reschedule,
            )

        button.callback = callback
        self.add_item(button)

    async def _confirm(self, interaction):
        if not await self._check(interaction, applicant=False):
            return
        await self._on_confirm(
            interaction, self._record["proposal_id"]
        )

    async def on_error(self, interaction, error, item):
        log_event(
            "welcome",
            "no_show_interaction_failed",
            guild_id=self._record["guild_id"],
            level=logging.ERROR,
            error=error,
        )
        content = (
            "操作を完了できませんでした。"
            "Welcomeの最新の案内を確認してください。"
            "保存済みの回答は取り消されません。"
        )
        if interaction.response.is_done():
            await _notice(interaction, content)
        else:
            await interaction.response.send_message(
                content,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
