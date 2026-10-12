"""本人待ちの期限。本番は48時間・72時間に固定する。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class WaitingLimits:
    reminder_seconds: int = 48 * 60 * 60
    timeout_seconds: int = 72 * 60 * 60

    def __post_init__(self):
        if (
            type(self.reminder_seconds) is not int
            or type(self.timeout_seconds) is not int
            or not 1 <= self.reminder_seconds < self.timeout_seconds
            or self.timeout_seconds > 2_147_483_647
        ):
            raise ValueError("リマインド期限より後に退出確認期限を設定してください。")

    @property
    def fingerprint(self):
        return f"{self.reminder_seconds}:{self.timeout_seconds}"


DEFAULT_LIMITS = WaitingLimits()


def limits_for_guild(guild):
    limits = WaitingLimits(
        reminder_seconds=guild.welcome_reminder_seconds,
        timeout_seconds=guild.welcome_timeout_seconds,
    )
    if guild.environment not in ("prod", "test"):
        raise ValueError("サーバーの環境設定が不正です。")
    if guild.environment == "prod" and limits != DEFAULT_LIMITS:
        raise ValueError("本番の本人期限は48時間・72時間に固定されています。")
    return limits


def duration_text(seconds):
    if type(seconds) is not int or seconds <= 0:
        raise ValueError("正の整数の秒数が必要です。")
    if seconds % 3600 == 0:
        return f"{seconds // 3600}時間"
    if seconds % 60 == 0:
        return f"{seconds // 60}分"
    return f"{seconds}秒"
