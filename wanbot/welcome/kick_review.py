"""面談不承認の理由入力と、管理者専用チャンネルへの確認投稿。"""

import discord

from wanbot.permissions import AccessDenied, require_manager
from wanbot.welcome.interview_gate import InterviewNotReady
from wanbot.welcome.kick_repository import (
    KickRequestConflict, normalize_reason,
)
from wanbot.welcome.kick_execution import KickExecution


async def check_review_channel(channel, *, client, config, guild_config):
    if (
        not isinstance(channel, discord.TextChannel)
        or channel.guild.id != guild_config.guild_id
        or channel.id != guild_config.ids["KICK_APPROVAL_CHANNEL_ID"]
        or client.user is None
    ):
        raise InterviewNotReady("キック確認チャンネルを確認できません。")

    guild = channel.guild
    if channel.overwrites_for(guild.default_role).view_channel is not False:
        raise InterviewNotReady(
            "キック確認チャンネルの@everyoneを閲覧拒否にしてください。"
        )

    for target, overwrite in channel.overwrites.items():
        if overwrite.view_channel is not True:
            continue

        is_role = isinstance(target, discord.Role) or (
            isinstance(target, discord.Object) and target.type is discord.Role
        )
        if is_role:
            if target.id not in guild_config.admin_role_ids:
                raise InterviewNotReady(
                    "キック確認チャンネルに、運営以外のロールの閲覧許可があります。"
                )
            continue

        if target.id == client.user.id:
            continue

        member = await guild.fetch_member(target.id)
        require_manager(
            config, guild.id,
            (role.id for role in member.roles),
            feature="welcome",
        )

    bot = await guild.fetch_member(client.user.id)
    permissions = channel.permissions_for(bot)
    if not all((
        permissions.view_channel,
        permissions.send_messages,
        permissions.embed_links,
        permissions.read_message_history,
    )):
        raise InterviewNotReady(
            "Botに確認チャンネルの閲覧・送信・埋め込み・履歴閲覧権限が必要です。"
        )


class KickReview:
    def __init__(
        self, *, client, config, guild_id, repository, admissions, lock,
        welcome_repository, archive,
    ):
        self._client = client
        self._config = config
        self._guild_id = guild_id
        self._repository = repository
        self._admissions = admissions
        self._lock = lock
        self._posted = set()
        self._review_views = {}
        self._executor = KickExecution(
            client=client,
            guild_id=guild_id,
            repository=repository,
            admissions=admissions,
            welcome_repository=welcome_repository,
            archive=archive,
        )

    async def _channel(self):
        guild_config = self._config.guild_for(self._guild_id)
        if guild_config is None or not guild_config.admin_role_ids:
            raise InterviewNotReady("管理者ロールの設定を確認してください。")
        channel = await self._client.fetch_channel(
            guild_config.ids["KICK_APPROVAL_CHANNEL_ID"]
        )
        await check_review_channel(
            channel,
            client=self._client,
            config=self._config,
            guild_config=guild_config,
        )
        return channel, guild_config

    async def request(self, controller, interaction, proposal_id, actor, reason):
        # 呼び出し元はWelcome共通ロックを取得済み。
        await controller._session()
        plan = await controller._plan()
        if (
            plan is None
            or plan.status != "confirmed"
            or plan.proposal_id != proposal_id
        ):
            raise InterviewNotReady("現在の確定済み面談ではありません。")
        if await self._admissions.get(controller._context.session_id) is not None:
            raise InterviewNotReady("この参加者はすでに参加承認されています。")

        reason = normalize_reason(reason)
        channel, guild_config = await self._channel()
        member = await channel.guild.fetch_member(controller._context.user_id)
        if member.joined_at is None:
            raise InterviewNotReady("今回のサーバー参加日時を確認できません。")
        await controller._session()

        record = await self._repository.create(
            context=controller._context,
            joined_at=member.joined_at,
            actor_id=actor.id,
            reason=reason,
        )
        await self._publish(record, channel, guild_config)
        await controller._notice(
            interaction,
            "不承認理由を保存し、管理者専用チャンネルへ確認依頼を投稿しました。\n"
            "まだキックは実行していません。",
        )


    async def request_no_show(self, controller, no_show):
        """保存済み不参加記録から依頼する。呼出元は共通ロックを取得する。"""
        context = controller._context
        if (
            no_show["status"] != "kick_required"
            or no_show["guild_id"] != self._guild_id
            or context.guild_id != self._guild_id
            or no_show["session_id"] != context.session_id
            or no_show["user_id"] != context.user_id
            or no_show["channel_id"] != context.channel_id
        ):
            raise InterviewNotReady("不参加確認の対象または状態が一致しません。")

        reasons = {
            "declined": "面談不参加後、本人が再調整しないと回答しました。",
            "timeout": "面談不参加の再調整確認に、1時間以内の回答がありませんでした。",
            "second_no_show": (
                "再調整した面談にも不参加で、管理者が最終確認しました。"
            ),
        }
        if no_show["reason"] not in reasons:
            raise InterviewNotReady("不参加の理由を確認できません。")

        await controller._session()
        plan = await controller._plan()
        if (
            plan is None
            or plan.status != "confirmed"
            or plan.proposal_id != no_show["proposal_id"]
        ):
            raise InterviewNotReady("現在の確定済み面談ではありません。")
        if await self._admissions.get(context.session_id) is not None:
            raise InterviewNotReady("この参加者はすでに参加承認されています。")

        # 面談IDを含め、別の面談・別の理由の依頼と区別する。
        reason = normalize_reason(
            reasons[no_show["reason"]]
            + f'（面談ID：{no_show["proposal_id"]}）'
        )
        existing = await self._repository.for_session(context.session_id)
        matching = [
            item for item in existing
            if item["reason"] == reason
            and item["guild_id"] == context.guild_id
            and item["user_id"] == context.user_id
            and item["channel_id"] == context.channel_id
        ]
        if len(matching) > 1:
            raise InterviewNotReady("同じ不参加の確認依頼が複数あります。")

        if matching:
            record = matching[0]
            # キャンセル済み・実行済みなどは再依頼しない。
            if record["status"] in ("pending", "approved"):
                key = (record["request_id"], record["status"])
                if key not in self._posted:
                    channel, guild_config = await self._channel()
                    await self._publish(record, channel, guild_config)
            return record

        if any(
            item["status"] in ("pending", "approved", "executing", "completed")
            for item in existing
        ):
            raise InterviewNotReady("別のキック確認依頼がすでにあります。")

        channel, guild_config = await self._channel()
        member = await channel.guild.fetch_member(context.user_id)
        if member.joined_at is None:
            raise InterviewNotReady("今回のサーバー参加日時を確認できません。")

        actor_id = (
            no_show["confirmed_by"]
            if no_show["reason"] == "second_no_show"
            else no_show["reported_by"]
        )
        if (
            type(actor_id) is not int
            or not 0 < actor_id < 2**64
            or actor_id == context.user_id
        ):
            raise InterviewNotReady("不参加を確認した管理者が不正です。")

        await controller._session()
        record = await self._repository.create(
            context=context,
            joined_at=member.joined_at,
            actor_id=actor_id,
            reason=reason,
        )
        await self._publish(record, channel, guild_config)
        return record

    def _view(self, record):
        request_id = record["request_id"]
        old = self._review_views.pop(request_id, None)
        if old is not None:
            old.stop()
        if record["status"] not in ("pending", "approved"):
            return None

        view = discord.ui.View(timeout=None)
        for approve, label, style in (
            (True, "キックを承認する", discord.ButtonStyle.danger),
            (False, "キャンセル", discord.ButtonStyle.secondary),
        ):
            button = discord.ui.Button(
                label=label,
                style=style,
                custom_id=f"wanbot:kick:{request_id}:{int(approve)}",
            )

            async def callback(interaction, choice=approve):
                await self._decide(interaction, request_id, choice)

            button.callback = callback
            view.add_item(button)

        self._review_views[request_id] = view
        return view

    async def _decide(self, interaction, request_id, approve):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            async with self._lock:
                channel, guild_config = await self._channel()
                if (
                    interaction.guild_id != self._guild_id
                    or interaction.channel_id != channel.id
                ):
                    raise AccessDenied("管理者専用の確認チャンネルで操作してください。")

                actor = await channel.guild.fetch_member(interaction.user.id)
                require_manager(
                    self._config,
                    self._guild_id,
                    (role.id for role in actor.roles),
                    feature="welcome",
                )
                if actor.bot:
                    raise AccessDenied("管理者本人が操作してください。")

                record = await self._repository.get(request_id)
                if record is None or record["guild_id"] != self._guild_id:
                    raise KickRequestConflict("確認依頼が見つかりません。")
                if actor.id == record["user_id"]:
                    raise AccessDenied("対象者本人は承認できません。")

                message = interaction.message
                marker = f"wanbot:kick-review:{request_id}"
                if (
                    message is None
                    or message.author.id != self._client.user.id
                    or message.webhook_id is not None
                    or not any(
                        item.footer.text == marker for item in message.embeds
                    )
                ):
                    raise AccessDenied("元の確認投稿から操作してください。")

                if approve:
                    record = await self._executor.approve(record, actor.id)
                    content = (
                        "キックとWelcomeのLog移動が完了しました。"
                        if record["status"] == "completed"
                        else "実行後の退出確認中です。キックは再実行しません。"
                    )
                else:
                    if record["status"] == "pending":
                        record = await self._repository.decide(
                            request_id, actor_id=actor.id, approve=False
                        )
                    elif record["status"] == "approved":
                        record = await self._repository.transition(
                            request_id, expected="approved", target="cancelled"
                        )
                    else:
                        raise KickRequestConflict("この依頼はキャンセルできません。")
                    content = "確認依頼をキャンセルしました。キックは実行していません。"

                await self._publish(record, channel, guild_config)

            await interaction.edit_original_response(
                content=content,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception as error:
            if isinstance(
                error, (AccessDenied, InterviewNotReady, KickRequestConflict)
            ):
                content = str(error)
            else:
                content = (
                    "処理を完了できませんでした。確認投稿の状態を確認してください。"
                    "実行結果が不明な場合、キックは自動で繰り返しません。"
                )
            await interaction.edit_original_response(
                content=content,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    async def _publish(self, record, channel, guild_config):
        marker = f"wanbot:kick-review:{record['request_id']}"
        message = None
        async for candidate in channel.history(limit=None):
            if (
                candidate.author.id == self._client.user.id
                and candidate.webhook_id is None
                and any(item.footer.text == marker for item in candidate.embeds)
            ):
                message = candidate
                break

        states = {
            "pending": "管理者の承認待ちです。まだキックしていません。",
            "approved": "承認済みです。実行にはボタンを押してください。",
            "executing": "実行結果の確認中です。キックは自動で再実行しません。",
            "completed": "退出確認とWelcomeの終了処理が完了しました。",
            "cancelled": "キャンセルされました。",
            "obsolete": "参加状態が変わったため、この依頼は無効になりました。",
        }
        embed = discord.Embed(
            title="キック確認依頼",
            description=states[record["status"]],
        )
        embed.add_field(
            name="対象者",
            value=f"<@{record['user_id']}>\\nID：{record['user_id']}",
            inline=False,
        )
        embed.add_field(name="理由", value=record["reason"], inline=False)
        embed.add_field(
            name="依頼元",
            value=f"<@{record['requested_by']}>",
            inline=False,
        )
        if record["decided_by"] is not None:
            embed.add_field(
                name="判断した管理者",
                value=f"<@{record['decided_by']}>",
                inline=False,
            )
        embed.add_field(
            name="Welcome",
            value=(
                f"https://discord.com/channels/{self._guild_id}/"
                f"{record['channel_id']}"
            ),
            inline=False,
        )
        if "waiting_timeout" in record:
            notice = getattr(self._executor, "_timeout_notice", None)
            notice_record = (
                await notice.get(record["request_id"])
                if notice is not None else None
            )
            labels = {
                "sent": "送信済み",
                "failed": "送信できませんでした",
                "unknown": "送信結果を確認できませんでした",
                "attempted": "以前の送信試行の結果が不明です。再送はしていません",
            }
            embed.add_field(
                name="キック前DM",
                value=(
                    "未送信"
                    if notice_record is None
                    else labels[notice_record["status"]]
                ),
                inline=False,
            )

        embed.set_footer(text=marker)
        view = self._view(record)

        mentions = discord.AllowedMentions.none()
        if message is None and record["status"] == "pending":
            mentions = discord.AllowedMentions(
                everyone=False, users=False, replied_user=False,
                roles=[
                    discord.Object(id=value)
                    for value in guild_config.admin_role_ids
                ],
            )
        options = {
            "embed": embed,
            "view": view,
            "allowed_mentions": mentions,
        }
        if message is None:
            options["content"] = " ".join(
                f"<@&{value}>" for value in sorted(guild_config.admin_role_ids)
            )
            await channel.send(**options)
        else:
            await message.edit(**options)
        self._posted.add((record["request_id"], record["status"]))

    async def restore(self):
        async with self._lock:
            records = await self._repository.all_reviews()
            if not records:
                return
            channel, guild_config = await self._channel()
            for record in records:
                if record["status"] == "executing":
                    record = await self._executor.reconcile(record)
                key = (record["request_id"], record["status"])
                if key not in self._posted:
                    await self._publish(record, channel, guild_config)

    def close(self):
        for view in self._review_views.values():
            view.stop()
        self._review_views.clear()


class RejectionReasonModal(discord.ui.Modal):
    def __init__(self, controller, proposal_id, owner_id):
        super().__init__(title="面談不承認の理由", timeout=600)
        self._controller = controller
        self._proposal_id = proposal_id
        self._owner_id = owner_id
        self._reason = discord.ui.TextInput(
            label="不承認理由",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=500,
        )
        self.add_item(self._reason)

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        async def operation(actor):
            await self._controller._kick_review.request(
                self._controller,
                interaction,
                self._proposal_id,
                actor,
                self._reason.value,
            )

        await self._controller._operate(
            interaction, operation, self._owner_id
        )

    async def on_error(self, interaction, error):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        await self._controller._meeting_error(interaction, error)


def rejection_button(controller, proposal_id, owner_id):
    button = discord.ui.Button(
        label="参加を承認しない",
        style=discord.ButtonStyle.danger,
    )

    async def callback(interaction):
        # Modalを開く前には応答を保留できないため、まず操作元を確認。
        # 送信時には_controller._operateで最新の管理者ロールを再確認する。
        try:
            if (
                interaction.guild_id != controller._context.guild_id
                or interaction.channel_id != controller._context.channel_id
                or interaction.user.id != owner_id
                or not isinstance(interaction.user, discord.Member)
            ):
                raise AccessDenied("この操作は画面を開いた管理者のみ実行できます。")
            require_manager(
                controller._meeting_config,
                interaction.guild_id,
                (role.id for role in interaction.user.roles),
                feature="welcome",
            )
        except AccessDenied:
            await interaction.response.send_message(
                "この操作は画面を開いた管理者のみ実行できます。",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            RejectionReasonModal(controller, proposal_id, owner_id)
        )

    button.callback = callback
    return button
