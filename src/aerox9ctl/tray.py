"""System tray icon showing the mouse's battery level."""

from __future__ import annotations

import fcntl
import os
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import config as cfg
from . import device
from .gui import DeviceWorker, MainWindow

POLL_MS = 60_000
LOW_LEVELS = (20, 10)  # notify once when dropping below each of these

COLOR_OK = QColor("#2ecc71")
COLOR_LOW = QColor("#f39c12")
COLOR_CRITICAL = QColor("#e74c3c")
COLOR_CHARGING = QColor("#3daee9")
COLOR_UNKNOWN = QColor("#7f8c8d")


def battery_color(level: int | None, charging: bool) -> QColor:
    if level is None:
        return COLOR_UNKNOWN
    if charging:
        return COLOR_CHARGING
    if level < 15:
        return COLOR_CRITICAL
    if level < 30:
        return COLOR_LOW
    return COLOR_OK


def render_icon(level: int | None, charging: bool = False, size: int = 64) -> QPixmap:
    """A battery glyph filled to ``level`` percent with the number on top."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    scale = size / 64
    color = battery_color(level, charging)
    # The outline uses the status color too, so it shows on dark and light panels alike.
    body = QRectF(3 * scale, 8 * scale, 52 * scale, 48 * scale)
    nub = QRectF(56 * scale, 22 * scale, 6 * scale, 20 * scale)
    painter.setPen(Qt.NoPen)
    painter.setBrush(color)
    painter.drawRoundedRect(nub, 2 * scale, 2 * scale)
    painter.setPen(QPen(color, 5 * scale))
    painter.setBrush(QColor(0, 0, 0, 90))
    painter.drawRoundedRect(body, 7 * scale, 7 * scale)

    inner = body.adjusted(6 * scale, 6 * scale, -6 * scale, -6 * scale)
    fraction = 1.0 if level is None else max(0, min(level, 100)) / 100
    fill = QRectF(inner.left(), inner.top(), max(inner.width() * fraction, 3 * scale), inner.height())
    painter.setPen(Qt.NoPen)
    painter.setBrush(color)
    painter.drawRoundedRect(fill, 2 * scale, 2 * scale)

    # Number with a dark outline so it stays readable on any fill and panel color
    text = "?" if level is None else str(level)
    font = QFont()
    font.setBold(True)
    font.setPixelSize(int((40 if len(text) < 3 else 30) * scale))
    path = QPainterPath()
    path.addText(0, 0, font, text)
    bounds = path.boundingRect()
    path.translate(body.center().x() - bounds.center().x(), body.center().y() - bounds.center().y())
    painter.setPen(QPen(QColor(0, 0, 0, 220), 5 * scale, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.drawPath(path)
    painter.fillPath(path, QColor("white"))
    painter.end()
    return pixmap


def status_text(name: str, battery: device.Battery | None, error: str) -> str:
    if error:
        return error
    if battery is None:
        return f"{name}: battery unavailable (asleep?)"
    return f"{name}: {battery.level}%, {'charging' if battery.charging else 'discharging'}"


class LowBatteryNotifier:
    """Decides when to warn: once per threshold while discharging."""

    def __init__(self, levels=LOW_LEVELS):
        self._levels = sorted(levels, reverse=True)
        self._warned: set[int] = set()

    def check(self, battery: device.Battery | None) -> int | None:
        """The threshold just crossed (warn about it), or None."""
        if battery is None:
            return None
        if battery.charging:
            self._warned.clear()
            return None
        self._warned = {level for level in self._warned if battery.level < level}
        crossed = [level for level in self._levels if battery.level < level and level not in self._warned]
        if not crossed:
            return None
        self._warned.update(crossed)
        return min(crossed)


class TrayApp(QObject):
    request_battery = Signal()
    request_apply = Signal(object, object, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._window: MainWindow | None = None
        self._notifier = LowBatteryNotifier()

        self.tray = QSystemTrayIcon(QIcon(render_icon(None)), self)
        self.tray.setToolTip("Aerox 9 Wireless")
        self.tray.activated.connect(self._on_activated)

        self.menu = QMenu()
        self.status_action = QAction("Looking for the mouse…", self.menu)
        self.status_action.setEnabled(False)
        self.menu.addAction(self.status_action)
        self.menu.addSeparator()
        self.menu.addAction("Open settings…", self.open_settings)
        self.profiles_menu = self.menu.addMenu("Apply profile")
        self.profiles_menu.aboutToShow.connect(self._fill_profiles_menu)
        self.menu.addAction("Refresh battery", self.request_battery.emit)
        self.menu.addSeparator()
        self.menu.addAction("Quit", QApplication.quit)
        self.tray.setContextMenu(self.menu)

        self._worker_thread = QThread(self)
        self._worker = DeviceWorker()
        self._worker.moveToThread(self._worker_thread)
        self.request_battery.connect(self._worker.read_battery)
        self.request_apply.connect(self._worker.apply)
        self._worker.battery_read.connect(self._on_battery)
        self._worker.applied.connect(self._on_applied)
        self._worker_thread.start()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.request_battery.emit)
        self._timer.start(POLL_MS)
        self.request_battery.emit()
        self.tray.show()

    def shutdown(self):
        self._timer.stop()
        self._worker_thread.quit()
        self._worker_thread.wait(3000)

    # --- slots ------------------------------------------------------------

    @Slot(object, str, str)
    def _on_battery(self, battery, name, error):
        text = status_text(name, battery, error)
        self.tray.setToolTip(text)
        self.status_action.setText(text)
        level = battery.level if battery else None
        self.tray.setIcon(QIcon(render_icon(level, bool(battery and battery.charging))))
        threshold = self._notifier.check(battery)
        if threshold is not None:
            self.tray.showMessage(
                "Aerox 9 battery low",
                f"Battery at {battery.level}% — time to charge the mouse.",
                QSystemTrayIcon.Warning if threshold > 10 else QSystemTrayIcon.Critical,
            )

    @Slot(object, str)
    def _on_applied(self, config, message):
        if config is None:
            self.tray.showMessage("Aerox 9", message, QSystemTrayIcon.Warning)
            return
        if self._window is not None:
            self._window._applied = config
            self._window.set_form(config)
        self.tray.showMessage("Aerox 9", message, QSystemTrayIcon.Information, 3000)

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.open_settings()

    def open_settings(self):
        if self._window is None:
            self._window = MainWindow()
            self._window.setAttribute(Qt.WA_DeleteOnClose)
            self._window.destroyed.connect(self._on_window_destroyed)
        self._window.show()
        self._window.raise_()
        self._window.activateWindow()

    def _on_window_destroyed(self, *_):
        self._window = None

    def _fill_profiles_menu(self):
        self.profiles_menu.clear()
        names = cfg.list_profiles()
        if not names:
            empty = self.profiles_menu.addAction("No saved profiles")
            empty.setEnabled(False)
        for name in names:
            self.profiles_menu.addAction(name, lambda name=name: self.apply_profile(name))

    def apply_profile(self, name: str):
        try:
            config = cfg.load_file(cfg.profile_path(name))
        except cfg.ConfigError as error:
            self.tray.showMessage("Aerox 9", f"Cannot load profile: {error}", QSystemTrayIcon.Warning)
            return
        groups = device.changed_groups(cfg.load_state(), config) or device.ALL_GROUPS
        self.request_apply.emit(config, groups, True)


def _single_instance_lock():
    """Keep a lock for the lifetime of the process; None if another tray runs."""
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    handle = open(Path(base) / "aerox9ctl-tray.lock", "a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def main() -> int:
    lock = _single_instance_lock()
    if lock is None:
        print("aerox9ctl tray is already running.", file=sys.stderr)
        return 1
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("aerox9ctl")
    app.setDesktopFileName("aerox9ctl")
    app.setQuitOnLastWindowClosed(False)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("error: no system tray available on this desktop.", file=sys.stderr)
        return 1
    tray = TrayApp()
    try:
        return app.exec()
    finally:
        tray.shutdown()
        lock.close()


if __name__ == "__main__":
    sys.exit(main())
