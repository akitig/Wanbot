"""確定済みの退出記録を送信し、再試行時は既存投稿を再利用する。"""

import asyncio

import discord

from wanbot.leave_log.models import decode_snapshot
from wanbot.leave_log.presentation import departure_embed


class DepartureDeliveryError(ValueError):
    pass


class DepartureDelivery:
    def __init__(
        self, *, client, config, guild_id, repository,
        results, is_available,
    ):
        self._client = client
        self._config = config
        self._guild_id = guild_id
        self._repository = repository
        self._results = results
        self._available = is_available
        self._lock = asyncio.Lock()

    async def send(self, event_id):
        async with self._lock:
            if not self._available():
                return None

            record = await self._repository.get(event_id)
            if record is None:
                raise DepartureDeliveryError("退出記録が見つかりません。")
            snapshot = decode_snapshot(record["snapshot"])
            if (
                snapshot.guild_id != self._guild_id
                or snapshot.event_id != event_id
            ):
                raise DepartureDeliveryError("退出記録の対象が一致しません。")

            # 送信済み記録を、無条件に再投稿しない。
            if record["message_id"] is not None:
                return record["message_id"]

            saved_result = await self._results.get(event_id)
            if saved_result is None:
                return None
            result_snapshot, result = saved_result
            if result_snapshot != snapshot:
                raise DepartureDeliveryError("照合結果の退出情報が一致しません。")

            config = self._config.guild_for(self._guild_id)
            if config is None or not config.feature_enabled("leave_log"):
                raise DepartureDeliveryError("退出記録が有効ではありません。")
            channel_id = config.ids["LEAVE_LOG_CHANNEL_ID"]
            channel = await self._client.fetch_channel(channel_id)
            if (
                not isinstance(channel, discord.TextChannel)
                or channel.id != channel_id
                or channel.guild.id != self._guild_id
                or channel.guild.unavailable
                or self._client.user is None
            ):
                raise DepartureDeliveryError("退出記録の送信先が一致しません。")

            bot = await channel.guild.fetch_member(self._client.user.id)
            permissions = channel.permissions_for(bot)
            if not all((
                permissions.view_channel,
                permissions.send_messages,
                permissions.embed_links,
                permissions.read_message_history,
            )):
                raise DepartureDeliveryError("退出記録の送信・履歴閲覧権限が不足しています。")

            marker = f"wanbot:leave-log:{event_id}"
            message = None
            async for candidate in channel.history(limit=None):
                if (
                    candidate.author.id == self._client.user.id
                    and candidate.webhook_id is None
                    and any(embed.footer.text == marker for embed in candidate.embeds)
                ):
                    message = candidate
                    break

            if not self._available():
                return None
            if message is None:
                message = await channel.send(
                    embed=departure_embed(snapshot, result),
                    allowed_mentions=discord.AllowedMentions.none(),
                )

            # 投稿できたら、途中で機能が停止しても送信先を保存する。
            await self._repository.mark_sent(
                event_id,
                channel_id=channel.id,
                message_id=message.id,
            )
            return message.id
