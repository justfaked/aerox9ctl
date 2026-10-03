import os
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from aerox9ctl import config as cfg  # noqa: E402
from aerox9ctl.gui import MainWindow  # noqa: E402


@pytest.fixture
def window():
    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    yield win
    win.close()
    app.processEvents()


def test_form_round_trip(window):
    config = cfg.MouseConfig(
        dpi_presets=(800, 1600, 3200), dpi_active=2, polling_rate=500, lighting="static",
        top_color="#112233", middle_color="#445566", bottom_color="#778899",
        reactive_color="#abcdef", brightness=40, startup_lighting="reactive", sleep_timer=10, dim_timer=0,
    )
    window.set_form(config)
    assert window.config_from_form() == config


def test_brightness_label_follows_slider(window):
    window.set_form(cfg.MouseConfig(brightness=0))
    assert window.brightness_label.text() == "0%"
    window.brightness.setValue(65)
    assert window.brightness_label.text() == "65%"


def test_shows_defaults_when_nothing_applied(window):
    assert window.config_from_form() == cfg.MouseConfig()
    assert "factory defaults" in window.statusBar().currentMessage()


def test_shrinking_preset_count_moves_active_preset(window):
    window.set_form(cfg.MouseConfig(dpi_active=5))
    window.dpi_count.setValue(2)
    config = window.config_from_form()
    assert config.dpi_presets == (400, 800)
    assert config.dpi_active == 2


def test_dirty_marker(window):
    window._applied = cfg.MouseConfig()
    window.set_form(cfg.MouseConfig())
    assert not window.windowTitle().startswith("*")
    window.polling.setCurrentIndex(window.polling.findData(125))
    assert window.windowTitle().startswith("*")


def test_apply_round_trip_through_worker(window, written):
    window.polling.setCurrentIndex(window.polling.findData(250))
    window._apply()
    app = QApplication.instance()
    for _ in range(200):
        app.processEvents()
        if window.apply_button.isEnabled():
            break
        time.sleep(0.01)
    assert cfg.load_state().polling_rate == 250
    assert window._applied.polling_rate == 250
    assert not window.windowTitle().startswith("*")


def test_buttons_unmanaged_by_default(window):
    assert window.config_from_form().buttons is None
    assert not window.button_combos["side1"].isEnabled()


def test_buttons_form_round_trip(window):
    config = cfg.MouseConfig(buttons={"side1": "F13", "side12": "VolumeUp", "button4": "disabled"})
    window.set_form(config)
    assert window.manage_buttons.isChecked()
    assert window.button_combos["side1"].isEnabled()
    assert window.config_from_form() == config


def test_invalid_button_action_is_reported_not_sent(window, written, monkeypatch):
    warnings = []
    monkeypatch.setattr("aerox9ctl.gui.QMessageBox.warning", lambda *args: warnings.append(args[2]))
    window.manage_buttons.setChecked(True)
    window.button_combos["side1"].setCurrentText("macro1")
    window._apply()
    assert warnings and "unknown action" in warnings[0]
    assert written == []


def test_live_tester(window, tmp_path, monkeypatch):
    fifo = tmp_path / "hidraw-fake"
    os.mkfifo(fifo)
    monkeypatch.setattr("aerox9ctl.tester.find_input_nodes", lambda: {0: fifo})
    window.tester_toggle.setChecked(True)
    writer = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
    os.write(writer, bytes([0b1000] + [0] * 11))  # button 4 down
    app = QApplication.instance()
    for _ in range(100):
        app.processEvents()
        if "button4" in window.tester_log.toPlainText():
            break
        time.sleep(0.01)
    os.close(writer)
    assert "pressed:  mouse button4" in window.tester_log.toPlainText()
    window.tester_toggle.setChecked(False)
    assert window._tester_fds == {}


def test_factory_defaults_keeps_buttons_tab(window):
    config = cfg.MouseConfig(polling_rate=125, buttons={"side1": "LeftShift+1"})
    window.set_form(config)
    window._factory_defaults()
    result = window.config_from_form()
    assert result.polling_rate == 1000
    assert result.buttons == config.buttons
