"""面談希望日時を日本時間で検証する。"""

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


JST = ZoneInfo("Asia/Tokyo")
DATE_PATTERN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}"
)
DATE_FORMAT = "%Y-%m-%d %H:%M"


class InterviewDateError(ValueError):
    """本人へ表示できる入力エラー。"""


def validate_dates(values, *, now):
    """3つの希望日時を検証し、日本時間のdatetimeのタプルを返す。"""
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError("Current time must include a timezone")

    if not isinstance(values, (list, tuple)) or len(values) != 3:
        raise InterviewDateError(
            "第一希望・第二希望・第三希望をすべて入力してください。"
        )

    current = now.astimezone(timezone.utc)
    dates = []

    for number, raw in enumerate(values, start=1):
        if not isinstance(raw, str):
            raise InterviewDateError(
                f"第{number}希望の日時を文字で入力してください。"
            )

        text = raw.strip()
        if DATE_PATTERN.fullmatch(text) is None:
            raise InterviewDateError(
                f"第{number}希望は「2026-10-20 21:00」の形式で"
                "年月日と時刻を入力してください。日本時間で扱います。"
            )

        try:
            value = datetime.strptime(text, DATE_FORMAT).replace(tzinfo=JST)
        except ValueError:
            raise InterviewDateError(
                f"第{number}希望の年月日・時刻が正しくありません。"
            ) from None

        if value.astimezone(timezone.utc) <= current:
            raise InterviewDateError(
                f"第{number}希望は、現在より後の日時を入力してください。"
            )

        dates.append(value)

    if len(set(dates)) != 3:
        raise InterviewDateError(
            "希望日時が重複しています。それぞれ別の日時を入力してください。"
        )

    return tuple(dates)


def format_date(value):
    """確認画面や運営への案内で使う日本時間の表示。"""
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Interview date must include a timezone")
    return value.astimezone(JST).strftime("%Y年%m月%d日 %H:%M（日本時間）")
