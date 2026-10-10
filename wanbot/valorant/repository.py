"""サーバー別に、各ユーザーの最新の診断結果を保存する。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from wanbot.config import AppConfig
from wanbot.storage.async_store import AsyncGuildJsonStore
from wanbot.storage.json_store import StorageError
from wanbot.valorant.scoring import ClassificationPolicy, DiagnosisScorer


RECORD_VERSION = 1


def _check_id(value: int) -> None:
    if type(value) is not int or not 0 < value < 2**64:
        raise ValueError("ユーザーIDは正のDiscord IDで指定してください。")


def _utc_time(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("日時にはタイムゾーンを指定してください。")
    return value.astimezone(timezone.utc)


def _make_record(
    scorer: DiagnosisScorer,
    *,
    user_id: int,
    invoker_id: int,
    answers: Mapping[str, str],
    completed_at: datetime,
) -> dict[str, Any]:
    _check_id(user_id)
    _check_id(invoker_id)
    timestamp = _utc_time(completed_at)

    # 保存前に全問の回答を検証し、回答から結果を計算する。
    # 呼び出し側が渡した判定結果を、そのまま信用して保存しない。
    result = scorer.evaluate(answers)

    return {
        "record_version": RECORD_VERSION,
        "user_id": user_id,
        "invoker_id": invoker_id,
        "completed_at": timestamp.isoformat(),
        "diagnosis_version": result.diagnosis_version,
        "answers": dict(answers),
        "policy": asdict(scorer.policy),
        "category": result.category.value,
        "weighted_score": result.weighted_score,
        "axes": {
            name: asdict(score)
            for name, score in result.axes.items()
        },
    }


class DiagnosisRepository:
    def __init__(
        self,
        config: AppConfig,
        guild_id: int,
        scorer: DiagnosisScorer,
    ) -> None:
        self._scorer = scorer
        self._store = AsyncGuildJsonStore(
            config,
            guild_id,
            "valorant_diagnoses",
        )

    def _validate_record(self, record: Any) -> None:
        try:
            if type(record) is not dict:
                raise ValueError

            if (
                type(record["record_version"]) is not int
                or record["record_version"] != RECORD_VERSION
            ):
                raise ValueError

            if (
                record["diagnosis_version"]
                != self._scorer.question_set.diagnosis_version
            ):
                raise ValueError

            # 現在の設定ではなく、保存時の判定基準で整合性を確認する。
            policy = ClassificationPolicy(**record["policy"])
            saved_scorer = DiagnosisScorer(
                self._scorer.question_set,
                policy=policy,
            )
            expected = _make_record(
                saved_scorer,
                user_id=record["user_id"],
                invoker_id=record["invoker_id"],
                answers=record["answers"],
                completed_at=datetime.fromisoformat(record["completed_at"]),
            )

            # JSON表現を比較し、余分な項目や型の違いも検出する。
            if json.dumps(record, sort_keys=True) != json.dumps(
                expected,
                sort_keys=True,
            ):
                raise ValueError

        except (KeyError, TypeError, ValueError, OverflowError):
            raise StorageError(
                "診断結果の形式または整合性が不正です。"
                "質問バージョンが異なる場合は移行が必要です。"
            ) from None

    def _validate_document(self, data: dict[str, Any]) -> None:
        if not data:
            return

        if (
            set(data) != {"latest"}
            or type(data["latest"]) is not dict
        ):
            raise StorageError("診断保存データの項目が不正です。")

        for user_key, record in data["latest"].items():
            self._validate_record(record)
            if user_key != str(record["user_id"]):
                raise StorageError("診断結果のユーザーIDが一致しません。")

    async def save_completed(
        self,
        *,
        user_id: int,
        invoker_id: int,
        answers: Mapping[str, str],
        completed_at: datetime | None = None,
    ) -> dict[str, Any]:
        record = _make_record(
            self._scorer,
            user_id=user_id,
            invoker_id=invoker_id,
            answers=answers,
            completed_at=(
                completed_at
                if completed_at is not None
                else datetime.now(timezone.utc)
            ),
        )

        def change(data: dict[str, Any]) -> dict[str, Any]:
            self._validate_document(data)
            latest = data.setdefault("latest", {})
            previous = latest.get(str(user_id))

            # 遅れて完了した保存処理で、新しい診断を巻き戻さない。
            if previous is not None:
                previous_time = datetime.fromisoformat(previous["completed_at"])
                current_time = datetime.fromisoformat(record["completed_at"])
                if previous_time > current_time:
                    return data

            latest[str(user_id)] = record
            return data

        await self._store.update(change)
        return record

    async def latest(self, user_id: int) -> dict[str, Any] | None:
        _check_id(user_id)
        data = await self._store.read()
        self._validate_document(data)
        return data.get("latest", {}).get(str(user_id))

    async def close(self) -> None:
        await self._store.close()
