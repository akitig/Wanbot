from types import SimpleNamespace

from wanbot.welcome.input_activity import InputActivity, finish_input


def test_input_blocks_movement_until_finished():
    activity = InputActivity(clock=lambda: 0)
    activity.touch("introduction", 300)
    assert activity.busy
    activity.finish("introduction")
    assert not activity.busy


def test_timeout_releases_activity():
    clock = [0]
    activity = InputActivity(clock=lambda: clock[0])
    activity.touch("interview", 600)
    clock[0] = 599
    assert activity.busy
    clock[0] = 600
    assert not activity.busy


def test_old_timeout_does_not_finish_new_input():
    activity = InputActivity(clock=lambda: 0)
    old = activity.touch("introduction", 300)
    activity.touch("introduction", 300)
    activity.finish("introduction", old)
    assert activity.busy


def test_completing_one_task_does_not_release_another():
    activity = InputActivity(clock=lambda: 0)
    activity.touch("diagnosis", 600)
    activity.touch("interview", 600)
    finish_input(SimpleNamespace(activity=activity), "diagnosis")
    assert activity.busy
    activity.finish("interview")
    assert not activity.busy
