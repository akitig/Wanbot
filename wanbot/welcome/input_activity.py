"""入力中はチェックリストの移動を止める。"""

import time


class InputActivity:
    def __init__(self, *, clock=time.monotonic):
        self._clock = clock
        self._entries = {}
        self._serial = 0

    def touch(self, kind, seconds):
        self._serial += 1
        token = self._serial
        self._entries[kind] = (token, self._clock() + seconds)
        return token

    def finish(self, kind, token=None):
        entry = self._entries.get(kind)
        if entry is not None and (token is None or entry[0] == token):
            del self._entries[kind]

    @property
    def busy(self):
        now = self._clock()
        for kind, (_, deadline) in tuple(self._entries.items()):
            if now >= deadline:
                del self._entries[kind]
        return bool(self._entries)


def activity_for(context):
    activity = getattr(context, "activity", None)
    return activity if isinstance(activity, InputActivity) else None


def finish_input(context, kind):
    activity = activity_for(context)
    if activity is not None:
        activity.finish(kind)


def track_input(context, view, kind, seconds):
    activity = activity_for(context)
    if activity is None:
        return

    token = activity.touch(kind, seconds)
    original_check = view.interaction_check
    original_timeout = view.on_timeout

    async def interaction_check(interaction):
        nonlocal token
        if not await context.check(interaction):
            return False
        if not await original_check(interaction):
            return False
        token = activity.touch(kind, seconds)
        return True

    async def on_timeout():
        # 古い画面のタイムアウトで、新しい入力画面を終了させない。
        activity.finish(kind, token)
        await original_timeout()

    view.interaction_check = interaction_check
    view.on_timeout = on_timeout
