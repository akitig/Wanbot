"""Discordに依存しない、VALORANT診断の採点・分類。"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

from wanbot.valorant.questions import QuestionSet, SUPPORTED_AXES


WEIGHTS = MappingProxyType({
    "win": 0.20,
    "team": 0.35,
    "improvement": 0.25,
    "focus": 0.20,
})


class AnswerError(ValueError):
    """未回答・不正な質問ID・不正な選択肢を検出した。"""


class Category(str, Enum):
    GACHI = "gachi"
    NEUTRAL = "neutral"
    ENJOY = "enjoy"


@dataclass(frozen=True)
class ClassificationPolicy:
    gachi_minimum: float = 0.65
    neutral_minimum: float = 0.35
    team_minimum: float = 0.60
    improvement_minimum: float = 5 / 9
    focus_minimum: float = 5 / 9

    def __post_init__(self) -> None:
        values = (
            self.gachi_minimum,
            self.neutral_minimum,
            self.team_minimum,
            self.improvement_minimum,
            self.focus_minimum,
        )
        if any(
            type(value) not in (int, float)
            or not math.isfinite(value)
            or not 0 <= value <= 1
            for value in values
        ):
            raise ValueError("判定基準は0〜1の有限な数値で指定してください。")

        if self.neutral_minimum >= self.gachi_minimum:
            raise ValueError("中立の下限はガチの下限より小さくしてください。")


@dataclass(frozen=True)
class AxisScore:
    score: int
    max_score: int
    normalized: float


@dataclass(frozen=True)
class DiagnosisResult:
    diagnosis_version: str
    category: Category
    weighted_score: float
    axes: Mapping[str, AxisScore]


class DiagnosisScorer:
    def __init__(
        self,
        question_set: QuestionSet,
        *,
        policy: ClassificationPolicy | None = None,
    ) -> None:
        self.question_set = question_set
        self.policy = policy if policy is not None else ClassificationPolicy()
        self._questions = {
            question.id: question for question in question_set.questions
        }
        self._maximums = {
            axis: sum(
                max(choice.scores.get(axis, 0) for choice in question.choices)
                for question in question_set.questions
            )
            for axis in SUPPORTED_AXES
        }

    def evaluate(self, answers: Mapping[str, str]) -> DiagnosisResult:
        """全問の回答を検証し、採点・判定する。"""
        if not isinstance(answers, Mapping):
            raise AnswerError("回答は質問IDと選択肢IDの対応で指定してください。")

        if set(answers) != set(self._questions):
            raise AnswerError("未回答、または不明な質問IDが含まれています。")

        totals = dict.fromkeys(SUPPORTED_AXES, 0)

        for question_id, question in self._questions.items():
            choice_id = answers[question_id]
            if type(choice_id) is not str:
                raise AnswerError("選択肢IDが不正です。")

            choice = next(
                (item for item in question.choices if item.id == choice_id),
                None,
            )
            if choice is None:
                raise AnswerError("選択肢IDが不正です。")

            for axis, points in choice.scores.items():
                totals[axis] += points

        axes = {
            axis: AxisScore(
                score=totals[axis],
                max_score=self._maximums[axis],
                normalized=(
                    totals[axis] / self._maximums[axis]
                    if self._maximums[axis]
                    else 0.0
                ),
            )
            for axis in SUPPORTED_AXES
        }

        # 判定前には丸めない。表示用の丸めは別処理にする。
        weighted_score = sum(
            axes[axis].normalized * weight
            for axis, weight in WEIGHTS.items()
        )

        policy = self.policy
        meets_gachi_axes = (
            axes["team"].normalized >= policy.team_minimum
            and axes["improvement"].normalized >= policy.improvement_minimum
            and axes["focus"].normalized >= policy.focus_minimum
        )

        if weighted_score >= policy.gachi_minimum and meets_gachi_axes:
            category = Category.GACHI
        elif weighted_score >= policy.neutral_minimum:
            category = Category.NEUTRAL
        else:
            category = Category.ENJOY

        return DiagnosisResult(
            diagnosis_version=self.question_set.diagnosis_version,
            category=category,
            weighted_score=weighted_score,
            axes=MappingProxyType(axes),
        )
