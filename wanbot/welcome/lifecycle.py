"""Welcomeの作成・退出・ログ移動の順序を管理する。"""

import asyncio
import logging

import discord

from wanbot.logging_config import log_event
from wanbot.welcome.channels import WelcomeChannelError


class WelcomeLifecycle:
    def __init__(
        self,
        *,
        guild_id,
        repository,
        channels,
        archive,
    ):
        self._guild_id = guild_id
        self._repository = repository
        self._channels = channels
        self._archive = archive
        self._lock = asyncio.Lock()

    @property
    def operation_lock(self):
        return self._lock

    def _check_member(self, member):
        if member.guild.id != self._guild_id or member.bot:
            raise WelcomeChannelError("Invalid Welcome member")

    async def _finish_archive(self, session):
        # Discord側の移動と権限確認が成功するまで、終了にしない。
        channel_id = await self._archive.archive(session)

        if channel_id is not None:
            session = await self._repository.record_archive_channel(
                session.session_id,
                channel_id,
            )

        return await self._repository.close_session(
            session.session_id,
            reason=session.closed_reason,
        )

    async def ensure(self, member):
        self._check_member(member)

        async with self._lock:
            previous = await self._repository.get_active(member.id)

            if previous is not None and previous.state == "archiving":
                await self._finish_archive(previous)

            return await self._channels.ensure(member)

    async def member_left(self, member):
        self._check_member(member)

        async with self._lock:
            session = await self._repository.get_active(member.id)
            if session is None:
                return None

            if session.state != "archiving":
                session = await self._repository.begin_archive(
                    session.session_id,
                    reason="member_left",
                )

            return await self._finish_archive(session)

    async def reconcile_members(self, guild):
        """停止中の退出を確認する。確認失敗は退出扱いにしない。"""
        if (
            guild is None
            or guild.id != self._guild_id
            or guild.unavailable
        ):
            raise WelcomeChannelError("Welcome guild is unavailable")

        completed = 0
        failed = 0

        async with self._lock:
            sessions = await self._repository.open_sessions()

            for session in sessions:
                try:
                    try:
                        member = await guild.fetch_member(session.user_id)
                    except discord.NotFound as error:
                        if error.code != 10007:
                            raise
                    else:
                        if (
                            member.id != session.user_id
                            or member.guild.id != self._guild_id
                        ):
                            raise WelcomeChannelError(
                                "Fetched member identity does not match"
                            )
                        # 現在も在籍しているので案内を維持する。
                        continue

                    pending = await self._repository.begin_archive(
                        session.session_id,
                        reason="member_left",
                    )
                    await self._finish_archive(pending)
                except Exception as error:
                    failed += 1
                    log_event(
                        "welcome",
                        "membership_reconciliation_failed",
                        guild_id=self._guild_id,
                        level=logging.ERROR,
                        error=error,
                    )
                else:
                    completed += 1

        return completed, failed

    async def resume_pending(self):
        """未完了の移動を再実行し、成功件数と失敗件数を返す。"""
        completed = 0
        failed = 0

        async with self._lock:
            sessions = await self._repository.pending_archives()

            for session in sessions:
                try:
                    await self._finish_archive(session)
                except Exception as error:
                    failed += 1
                    log_event(
                        "welcome",
                        "archive_retry_failed",
                        guild_id=self._guild_id,
                        level=logging.ERROR,
                        error=error,
                    )
                else:
                    completed += 1

        return completed, failed
