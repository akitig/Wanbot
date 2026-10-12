"""面談希望日時の入力・確定前に、Welcomeの進行条件を確認する。"""

from wanbot.valorant.session import SessionStatus


class InterviewNotReady(ValueError):
    """本人へ表示できる、面談に進めない理由。"""


class InterviewGate:
    def __init__(
        self,
        *,
        context,
        welcome_repository,
        questionnaire_repository,
        rules_checker,
        load_diagnosis,
    ):
        self._context = context
        self._welcome = welcome_repository
        self._questions = questionnaire_repository
        self._rules = rules_checker

        # 今回のWelcome専用の診断を読み込む関数を渡す。
        # 過去の最新診断結果を使って完了判定しない。
        self._load_diagnosis = load_diagnosis

    async def _active_session(self):
        if not self._context.is_available():
            raise InterviewNotReady(
                "現在、このWelcomeでは面談希望日時を受け付けていません。"
            )

        session = await self._welcome.get_active(self._context.user_id)
        if (
            session is None
            or session.state != "active"
            or session.session_id != self._context.session_id
            or session.user_id != self._context.user_id
            or session.channel_id != self._context.channel_id
        ):
            raise InterviewNotReady(
                "このWelcomeは終了しているか、現在の案内ではありません。"
            )

    async def check(self):
        """呼び出し元で本人確認とWelcome共通ロックの取得を行う。"""
        await self._active_session()

        snapshot = await self._questions.get(self._context.session_id)
        if (
            snapshot is None
            or snapshot.session_id != self._context.session_id
            or snapshot.user_id != self._context.user_id
            or snapshot.progress.status != "completed"
        ):
            raise InterviewNotReady(
                "先に、参加者への質問をすべて回答してください。"
            )

        plays_valo = snapshot.progress.plays_valo
        if plays_valo is True:
            progress = await self._load_diagnosis()
            if (
                progress is None
                or progress.session.guild_id != self._context.guild_id
                or progress.session.user_id != self._context.user_id
                or progress.session.status is not SessionStatus.COMPLETED
                or progress.result is None
            ):
                raise InterviewNotReady(
                    "先に、このWelcomeのVALORANT診断を完了してください。"
                )
        elif plays_valo is not False:
            raise InterviewNotReady(
                "VALORANTをやるかどうかの回答を確認してください。"
            )

        # 以前の確認結果は使わず、確定時にもDiscordから確認する。
        if not await self._rules.has_reacted(self._context.user_id):
            raise InterviewNotReady(
                "ルール投稿に指定のリアクションを付けてから、"
                "もう一度お試しください。"
            )

        # Discordとの通信中に機能が停止した場合も進めない。
        await self._active_session()
