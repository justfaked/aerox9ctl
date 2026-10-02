import os
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from aerox9ctl import config as cfg  # noqa: E402
from aerox9ctl import tray  # noqa: E402
from aerox9ctl.device import Battery  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tray_app(app):
    instance = tray.TrayApp()
    yield instance
    if instance._window is not None:
        instance._window.close()
    instance.shutdown()
    app.processEvents()


def wait_for(condition, app, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


def discharging(level):
    return Battery(level=level, charging=False)


def test_notifier_warns_once_per_threshold():
    notifier = tray.LowBatteryNotifier()
    assert notifier.check(discharging(50)) is None
    assert notifier.check(discharging(19)) == 20
    assert notifier.check(discharging(18)) is None  # already warned
    assert notifier.check(discharging(9)) == 10
    assert notifier.check(discharging(8)) is None
    assert notifier.check(None) is None  # asleep: no change


def test_notifier_resets_when_charging():
    notifier = tray.LowBatteryNotifier()
    notifier.check(discharging(15))
    assert notifier.check(Battery(level=15, charging=True)) is None
    assert notifier.check(discharging(15)) == 20


def test_notifier_starting_very_low_warns_once_with_lowest_threshold():
    notifier = tray.LowBatteryNotifier()
    assert notifier.check(discharging(5)) == 10
    assert notifier.check(discharging(5)) is None


def test_status_text():
    assert tray.status_text("Aerox", discharging(80), "") == "Aerox: 80%, discharging"
    assert tray.status_text("Aerox", Battery(50, True), "") == "Aerox: 50%, charging"
    assert "unavailable" in tray.status_text("Aerox", None, "")
    assert tray.status_text("", None, "no mouse") == "no mouse"


@pytest.mark.parametrize(
    "level, charging, color",
    [(80, False, tray.COLOR_OK), (25, False, tray.COLOR_LOW), (10, False, tray.COLOR_CRITICAL),
     (10, True, tray.COLOR_CHARGING), (None, False, tray.COLOR_UNKNOWN)],
)
def test_battery_color(level, charging, color):
    assert tray.battery_color(level, charging) == color


@pytest.mark.parametrize("level", [None, 0, 7, 80, 100])
def test_render_icon(app, level):
    pixmap = tray.render_icon(level, charging=False, size=64)
    assert pixmap.width() == 64 and not pixmap.toImage().isNull()


def test_tray_shows_battery_from_device(tray_app, app, monkeypatch):
    # The simulated device never answers, like a sleeping mouse
    assert wait_for(lambda: "unavailable" in tray_app.tray.toolTip(), app)
    assert "Aerox 9 Wireless (2.4 GHz)" in tray_app.status_action.text()


def test_low_battery_notification(tray_app, monkeypatch):
    messages = []
    monkeypatch.setattr(tray_app.tray, "showMessage", lambda *args: messages.append(args))
    tray_app._on_battery(discharging(12), "Aerox", "")
    assert tray_app.tray.toolTip() == "Aerox: 12%, discharging"
    assert messages and "12%" in messages[0][1]


def test_profiles_menu_and_apply(tray_app, app, written):
    tray_app._fill_profiles_menu()
    assert [a.text() for a in tray_app.profiles_menu.actions()] == ["No saved profiles"]
    cfg.save_file(cfg.MouseConfig(polling_rate=500), cfg.profile_path("fps"))
    tray_app._fill_profiles_menu()
    action = tray_app.profiles_menu.actions()[0]
    assert action.text() == "fps"
    action.trigger()
    assert wait_for(lambda: cfg.load_state() is not None, app)
    assert cfg.load_state().polling_rate == 500


def test_open_settings_reuses_window(tray_app, app):
    tray_app.open_settings()
    first = tray_app._window
    tray_app.open_settings()
    assert tray_app._window is first
    first.close()
    assert wait_for(lambda: tray_app._window is None, app)


def test_single_instance_lock(monkeypatch, tmp_path):
    first = tray._single_instance_lock()
    assert first is not None
    assert tray._single_instance_lock() is None
    first.close()
    second = tray._single_instance_lock()
    assert second is not None
    second.close()
