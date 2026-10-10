"""保存済みの診断を、管理者向けのページに変換する。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from wanbot.valorant.presentation import format_result
from wanbot.valorant.questions import QuestionSet
from wanbot.valorant.scoring import ClassificationPolicy, DiagnosisScorer


JST = timezone(timedelta(hours=9))
MAX_BODY_LENGTH = 1900
QUESTIONS_PER_PAGE = 5


def format_log_pages(
    record: dict[str, Any],
    question_set: QuestionSet,
) -> tuple[str, ...]:
    """Repositoryで検証済みの最新結果から、表示用ページを作る。"""
    if record["diagnosis_version"] != question_set.diagnosis_version:
        raise ValueError("保存済み診断と質問のバージョンが一致しません。")

    # 現在の設定ではなく、診断を保存したときの判定基準を使う。
    scorer = DiagnosisScorer(
        question_set,
        policy=ClassificationPolicy(**record["policy"]),
    )
    result = scorer.evaluate(record["answers"])

    completed_at = datetime.fromisoformat(record["completed_at"])
    if completed_at.utcoffset() is None:
        raise ValueError("診断完了日時にタイムゾーンがありません。")
    completed_text = completed_at.astimezone(JST).strftime("%Y-%m-%d %H:%M:%S")

    summary = (
        "【VALORANT診断・管理者用】\n"
        f"回答者ID：{record['user_id']}\n"
        f"依頼した管理者ID：{record['invoker_id']}\n"
        f"完了日時：{completed_text}（日本時間）\n"
        f"診断バージョン：{record['diagnosis_version']}\n\n"
        + format_result(result, include_total=True)
    )
    if len(summary) > MAX_BODY_LENGTH:
        raise ValueError("診断概要が表示上限を超えています。")

    pages = [summary]
    blocks: list[str] = []
    heading = "【質問と回答】\n\n"

    for number, question in enumerate(question_set.questions, start=1):
        selected_id = record["answers"][question.id]
        selected = next(
            choice for choice in question.choices
            if choice.id == selected_id
        )
        block = (
            f"Q{number:02d}．{question.text}\n"
            f"回答 {selected.id.upper()}：{selected.text}"
        )

        if len(heading + block) > MAX_BODY_LENGTH:
            raise ValueError("質問と回答が1ページの表示上限を超えています。")

        candidate = heading + "\n\n".join([*blocks, block])
        if blocks and (
            len(blocks) >= QUESTIONS_PER_PAGE
            or len(candidate) > MAX_BODY_LENGTH
        ):
            pages.append(heading + "\n\n".join(blocks))
            blocks = []

        blocks.append(block)

    if blocks:
        pages.append(heading + "\n\n".join(blocks))

    total = len(pages)
    return tuple(
        f"{body}\n\nページ {number} / {total}"
        for number, body in enumerate(pages, start=1)
    )
