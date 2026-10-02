from aerox9ctl import daemon, device
from aerox9ctl.config import MouseConfig


def run(online_sequence, state=MouseConfig(lighting="static"), apply=None):
    sequence = iter(online_sequence)
    applied = []

    def record(config, groups, save):
        applied.append((config, set(groups), save))

    daemon.run(
        interval=0,
        is_online=lambda: next(sequence),
        load_state=lambda: state,
        apply=apply or record,
        sleep=lambda _: None,
        max_iterations=len(online_sequence),
    )
    return applied


def test_reapplies_lighting_each_time_the_mouse_comes_online():
    applied = run([False, True, True, False, False, True])
    assert len(applied) == 2
    config, groups, save = applied[0]
    assert groups == {device.GROUP_LIGHTING}
    assert save is False  # never wear out the onboard memory


def test_does_nothing_without_state():
    assert run([True, True], state=None) == []


def test_retries_after_failed_apply():
    attempts = []

    def flaky(config, groups, save):
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("busy")

    run([True, True, True], apply=flaky)
    assert len(attempts) == 2


def test_survives_os_errors_while_polling():
    calls = iter([OSError("gone"), True])

    def is_online():
        value = next(calls)
        if isinstance(value, Exception):
            raise value
        return value

    applied = []
    daemon.run(
        interval=0, is_online=is_online, load_state=MouseConfig, sleep=lambda _: None,
        apply=lambda *a, **k: applied.append(1), max_iterations=2,
    )
    assert applied == [1]


def test_default_online_check_treats_missing_mouse_as_offline(monkeypatch):
    def missing():
        raise device.DeviceNotFound()

    monkeypatch.setattr(device, "read_battery", missing)
    assert daemon._is_online() is False
