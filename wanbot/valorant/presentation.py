"""VALORANT診断結果を、Discordに依存しない文章に変換する。"""

from wanbot.valorant.scoring import Category, DiagnosisResult

CATEGORY_PRESENTATION = {
    Category.GACHI: (
        "🔥 ガチ",
        "チームで相談しながら試合を組み立て、\n"
        "うまくいかなかったところを改善しながら\n"
        "勝利を目指すプレイスタイルです。\n"
        "必要な場面ではしっかり集中し、\n"
        "チームゲームとしてVALORANTを楽しむ傾向があります。",
    ),
    Category.NEUTRAL: (
        "⚖️ 中立",
        "勝ちを目指すことと、\n"
        "楽しく遊ぶことの両方を大切にするプレイスタイルです。\n"
        "メンバーや状況に合わせながら、\n"
        "チームとして遊ぶことを大切にする傾向があります。",
    ),
    Category.ENJOY: (
        "🥳 エンジョイ",
        "勝敗や細かな振り返りより、\n"
        "みんなで楽しく遊べることを大切にするプレイスタイルです。\n"
        "チームゲームには参加しつつ、\n"
        "雰囲気や自由度を重視する傾向があります。",
    ),
}

AXIS_LABELS = (
    ("win", "🏆 勝利志向"),
    ("team", "🤝 チーム志向"),
    ("improvement", "🔧 改善志向"),
    ("focus", "🎯 集中志向"),
)

FEEDBACK_RECEIVE = {
    3: "積極的に提案やアドバイスを受けたい",
    2: "理由が分かれば提案を取り入れやすい",
    1: "必要な場面で軽く提案されるくらいが合いやすい",
    0: "基本的には自分で考えてプレイすることを好む",
}

FEEDBACK_GIVE = {
    3: "必要だと思ったことは、その場で簡潔に伝える",
    2: "状況に合わせて、相談や振り返りの形で伝える",
    1: "必要な場面に絞って伝える",
    0: "基本的には相手のプレイ判断を尊重する",
}

INTRODUCTION = (
    "この診断は、VALORANTの「上手い・下手」を決めるものではありません！\n"
    "\n"
    "普段どんなふうにコンペを遊びたいかを確認して、\n"
    "灯麗会で一緒に遊ぶ人とのミスマッチを減らすための診断です。\n"
    "\n"
    "灯麗会では、エンジョイ・ガチに関係なく、\n"
    "\n"
    "・故意に試合を投げない\n"
    "・試合に必要なコミュニケーションを取る\n"
    "・必要な場面では試合を優先する\n"
    "\n"
    "ことを前提としています。\n"
    "\n"
    "「できるか」ではなく、\n"
    "普段どう遊びたいかで答えてください！\n"
    "\n"
    "全15問です。"
)


def _percentage(value: float) -> int:
    return round(max(0.0, min(1.0, value)) * 100)


def _bar(value: float) -> str:
    filled = round(max(0.0, min(1.0, value)) * 10)
    return "█" * filled + "░" * (10 - filled)


def format_result(
    result: DiagnosisResult,
    *,
    include_total: bool = False,
) -> str:
    """本人向けの結果を作る。管理者向けには総合スコアも表示できる。"""
    title, description = CATEGORY_PRESENTATION[result.category]
    lines = [title, "", description, ""]

    if include_total:
        lines.extend(
            [
                f"総合スコア：{_percentage(result.weighted_score)}%",
                "",
            ]
        )

    lines.extend(["━━━━━━━━━━━━━━", ""])

    for axis_name, label in AXIS_LABELS:
        value = result.axes[axis_name].normalized
        lines.extend(
            [
                label,
                f"{_bar(value)} {_percentage(value)}%",
                "",
            ]
        )

    receive = FEEDBACK_RECEIVE[result.axes["feedback_receive"].score]
    give = FEEDBACK_GIVE[result.axes["feedback_give"].score]

    lines.extend(
        [
            "━━━━━━━━━━━━━━",
            "",
            "💬 フィードバック傾向",
            f"・{receive}",
            f"・{give}",
            "",
            "※この診断は実力やランクを評価するものではありません。",
        ]
    )

    return "\n".join(lines)
