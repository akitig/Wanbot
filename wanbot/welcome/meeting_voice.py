"""確定した面談の専用VCを作成し、保存・再利用する。"""

import asyncio
import re

import discord

from wanbot.permissions import require_manager
from wanbot.storage.async_store import AsyncGuildJsonStore
from wanbot.welcome.channels import permission_signature
from wanbot.welcome.interview_dates import JST
from wanbot.welcome.interview_gate import InterviewNotReady


def meeting_member_ids(plan):
    values = [plan.user_id, plan.first_manager_id]
    if plan.second_manager_id is not None:
        values.append(plan.second_manager_id)
    if (
        any(type(value) is not int or value <= 0 for value in values)
        or len(set(values)) != len(values)
    ):
        raise InterviewNotReady("面談参加者の設定が不正です。")
    return tuple(values)


def meeting_voice_name(plan):
    return plan.meeting_at.astimezone(JST).strftime("%Y-%m-%d %H時%M分")


def meeting_overwrites(default_role, members, bot):
    overwrites = {
        default_role: discord.PermissionOverwrite(
            view_channel=False,
            connect=False,
        )
    }
    for member in (*members, bot):
        overwrites[member] = discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            send_messages=True,
            read_message_history=True,
        )
    return overwrites


class MeetingVoice:
    def __init__(self, *, client, config, guild_id, store=None):
        self._client = client
        self._config = config
        self._guild_id = guild_id
        self._store = (
            store if store is not None
            else AsyncGuildJsonStore(config, guild_id, "welcome_meeting_vcs")
        )
        self._lock = asyncio.Lock()

    def _records(self, data):
        if not data:
            return {}
        if (
            type(data) is not dict
            or set(data) != {"version", "records"}
            or type(data["version"]) is not int
            or data["version"] != 1
            or type(data["records"]) is not dict
        ):
            raise InterviewNotReady("面談VCの保存形式が不正です。")
        return data["records"]

    def _record(self, data, proposal_id, identity):
        record = self._records(data).get(proposal_id)
        if record is None:
            return None
        if (
            type(record) is not dict
            or set(record) != {"identity", "channel_id"}
            or record["identity"] != identity
        ):
            raise InterviewNotReady("保存された面談VCの対象が一致しません。")
        channel_id = record["channel_id"]
        if channel_id is not None and (
            type(channel_id) is not int or not 0 < channel_id < 2**64
        ):
            raise InterviewNotReady("保存された面談VCのIDが不正です。")
        return record

    async def _save(self, proposal_id, identity, channel_id):
        def change(data):
            records = self._records(data)
            previous = self._record(data, proposal_id, identity)
            if previous is not None:
                saved_id = previous["channel_id"]
                if saved_id is not None and saved_id != channel_id:
                    raise InterviewNotReady("別の面談VCがすでに保存されています。")
            records[proposal_id] = {
                "identity": identity,
                "channel_id": channel_id,
            }
            return {"version": 1, "records": records}

        await self._store.update(change)

    def _check_channel(self, channel, category, overwrites):
        if (
            not isinstance(channel, discord.VoiceChannel)
            or channel.guild.id != self._guild_id
            or channel.category_id != category.id
        ):
            raise InterviewNotReady("面談VCの所属またはカテゴリが一致しません。")
        if permission_signature(channel.overwrites) != permission_signature(
            overwrites
        ):
            raise InterviewNotReady("面談VCの権限が想定と一致しません。")

    async def ensure(self, plan):
        async with self._lock:
            return await self._ensure(plan)

    async def _ensure(self, plan):
        if (
            plan.status != "confirmed"
            or plan.guild_id != self._guild_id
            or re.fullmatch(r"[0-9a-f]{32}", plan.proposal_id) is None
            or re.fullmatch(r"[0-9a-f]{32}", plan.welcome_session_id) is None
        ):
            raise InterviewNotReady("確定した面談を確認できません。")

        participant_ids = meeting_member_ids(plan)
        guild_config = self._config.guild_for(self._guild_id)
        if guild_config is None or not guild_config.feature_enabled("welcome"):
            raise InterviewNotReady("このサーバーではWelcomeを利用できません。")

        guild = self._client.get_guild(self._guild_id)
        if (
            guild is None or guild.unavailable
            or self._client.user is None
        ):
            raise InterviewNotReady("サーバーを確認できません。")

        category_id = guild_config.ids["MEETING_CATEGORY_ID"]
        category = await self._client.fetch_channel(category_id)
        if (
            not isinstance(category, discord.CategoryChannel)
            or category.id != category_id
            or category.guild.id != self._guild_id
        ):
            raise InterviewNotReady("Meetingカテゴリを確認できません。")

        bot = await guild.fetch_member(self._client.user.id)
        permissions = category.permissions_for(bot)
        if not permissions.manage_channels or not permissions.manage_roles:
            raise InterviewNotReady(
                "BotにMeetingカテゴリのチャンネル管理・権限管理が必要です。"
            )

        members = []
        for user_id in participant_ids:
            member = await guild.fetch_member(user_id)
            if member.bot:
                raise InterviewNotReady("Botを面談参加者には指定できません。")
            if user_id != plan.user_id:
                require_manager(
                    self._config,
                    self._guild_id,
                    (role.id for role in member.roles),
                    feature="welcome",
                )
            members.append(member)

        overwrites = meeting_overwrites(guild.default_role, members, bot)
        identity = {
            "session_id": plan.welcome_session_id,
            "guild_id": plan.guild_id,
            "welcome_channel_id": plan.channel_id,
            "category_id": category_id,
            "participants": list(participant_ids),
            "meeting_at": plan.meeting_at.isoformat(),
        }
        record = self._record(
            await self._store.read(), plan.proposal_id, identity
        )
        temporary_name = f"wanbot-meeting-{plan.proposal_id}"

        if record is not None and record["channel_id"] is not None:
            # 消されていても、無条件に作り直して増殖させない。
            channel = await self._client.fetch_channel(record["channel_id"])
        else:
            channels = await guild.fetch_channels()
            matches = [
                item for item in channels
                if item.name == temporary_name
            ]
            if len(matches) > 1:
                raise InterviewNotReady("同じ面談の仮VCが複数あります。")

            if matches:
                if record is None:
                    raise InterviewNotReady("保存記録のない仮VCがあります。")
                channel = matches[0]
            elif record is not None:
                # Discordへの作成依頼が成功したか不明な場合、
                # 再作成せず停止して重複を防ぐ。
                raise InterviewNotReady(
                    "面談VCの作成結果を確認できません。"
                    "管理者がMeetingカテゴリとログを確認してください。"
                )
            else:
                await self._save(plan.proposal_id, identity, None)
                channel = await guild.create_voice_channel(
                    name=temporary_name,
                    category=category,
                    overwrites=overwrites,
                    reason="Wanbot meeting",
                )

            self._check_channel(channel, category, overwrites)
            await self._save(plan.proposal_id, identity, channel.id)

        self._check_channel(channel, category, overwrites)
        name = meeting_voice_name(plan)
        if channel.name != name:
            await channel.edit(name=name, reason="Wanbot meeting date")
            channel = await self._client.fetch_channel(channel.id)
            self._check_channel(channel, category, overwrites)
            if channel.name != name:
                raise InterviewNotReady("面談VCの名前を更新できませんでした。")
        return channel

    async def cleanup_records(self):
        result = []
        for proposal_id, record in self._records(
            await self._store.read()
        ).items():
            if (
                re.fullmatch(r"[0-9a-f]{32}", proposal_id) is None
                or type(record) is not dict
                or type(record.get("identity")) is not dict
            ):
                raise InterviewNotReady("面談VCの保存記録が不正です。")

            identity = record["identity"]
            self._record(
                {"version": 1, "records": {proposal_id: record}},
                proposal_id,
                identity,
            )
            if record["channel_id"] is None:
                continue
            if (
                identity.get("guild_id") != self._guild_id
                or not isinstance(identity.get("session_id"), str)
                or re.fullmatch(
                    r"[0-9a-f]{32}", identity["session_id"]
                ) is None
                or type(identity.get("participants")) is not list
                or not identity["participants"]
                or any(
                    type(value) is not int or value <= 0
                    for value in identity["participants"]
                )
                or type(identity.get("category_id")) is not int
                or type(identity.get("welcome_channel_id")) is not int
            ):
                raise InterviewNotReady("面談VCの保存対象が不正です。")
            result.append({
                "proposal_id": proposal_id,
                "channel_id": record["channel_id"],
                "identity": dict(identity),
            })
        return tuple(result)

    async def close(self):
        await self._store.close()
