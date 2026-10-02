"""Qt (PySide6) graphical interface."""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import QObject, QSocketNotifier, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QFontDatabase, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import buttons as btn
from . import config as cfg
from . import device
from . import tester

BATTERY_POLL_MS = 30_000

LIGHTING_LABELS = {"static": "Static colors", "rainbow": "Rainbow"}
STARTUP_LABELS = {
    "off": "Off",
    "reactive": "Off, flash on click",
    "rainbow": "Rainbow",
    "reactive-rainbow": "Rainbow, flash on click",
}


class DeviceWorker(QObject):
    """Runs all device I/O off the GUI thread."""

    battery_read = Signal(object, str, str)  # Battery | None, device description, error
    applied = Signal(object, str)  # applied MouseConfig (None on failure), message

    @Slot()
    def read_battery(self):
        try:
            name = device.describe()
            self.battery_read.emit(device.read_battery(), name, "")
        except device.DeviceNotFound as error:
            self.battery_read.emit(None, "", str(error))
        except OSError as error:
            self.battery_read.emit(None, "", f"Cannot open the mouse: {error}")

    @Slot(object, object, bool)
    def apply(self, config, groups, save):
        try:
            device.apply(config, groups, save=save)
            cfg.save_state(config)
        except (device.DeviceNotFound, OSError) as error:
            self.applied.emit(None, f"Apply failed: {error}")
            return
        self.applied.emit(config, "Applied" + (" and saved to the mouse." if save else "."))


class ColorButton(QPushButton):
    changed = Signal()

    def __init__(self, color="#ffffff", parent=None):
        super().__init__(parent)
        self.setMinimumWidth(90)
        self.clicked.connect(self._pick)
        self.set_color(color)

    def color(self) -> str:
        return self._color

    def set_color(self, color: str):
        self._color = color
        qcolor = QColor(color)
        text = "#000000" if qcolor.lightness() > 128 else "#ffffff"
        self.setText(color)
        self.setStyleSheet(f"background-color: {color}; color: {text};")
        self.changed.emit()

    def _pick(self):
        picked = QColorDialog.getColor(QColor(self._color), self, "Pick a color")
        if picked.isValid():
            self.set_color(picked.name())


def _spin(minimum, maximum, step=1, suffix="", never=False) -> QSpinBox:
    box = QSpinBox()
    box.setRange(minimum, maximum)
    box.setSingleStep(step)
    box.setSuffix(suffix)
    if never:
        box.setSpecialValueText("Never")
    return box


class MainWindow(QMainWindow):
    request_battery = Signal()
    request_apply = Signal(object, object, bool)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Aerox 9 Wireless")
        self.setWindowIcon(QIcon.fromTheme("input-mouse"))
        self._applied = cfg.load_state()  # None: unknown mouse state
        self._loading = False

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addLayout(self._build_header())
        columns = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(self._build_dpi_group())
        left.addWidget(self._build_power_group())
        left.addStretch()
        columns.addLayout(left)
        right = QVBoxLayout()
        right.addWidget(self._build_lighting_group())
        right.addStretch()
        columns.addLayout(right)
        general = QWidget()
        general.setLayout(columns)
        tabs = QTabWidget()
        tabs.addTab(general, "General")
        tabs.addTab(self._build_buttons_tab(), "Buttons")
        layout.addWidget(tabs)
        layout.addWidget(self._build_profiles_group())
        layout.addLayout(self._build_action_row())
        self.setCentralWidget(central)

        self._worker_thread = QThread(self)
        self._worker = DeviceWorker()
        self._worker.moveToThread(self._worker_thread)
        self.request_battery.connect(self._worker.read_battery)
        self.request_apply.connect(self._worker.apply)
        self._worker.battery_read.connect(self._on_battery)
        self._worker.applied.connect(self._on_applied)
        self._worker_thread.start()

        self.set_form(self._applied or cfg.MouseConfig())
        if self._applied is None:
            self.statusBar().showMessage(
                "Showing factory defaults: the mouse's current settings can't be read back."
            )
        self._refresh_profiles()

        self._battery_timer = QTimer(self)
        self._battery_timer.timeout.connect(self.request_battery.emit)
        self._battery_timer.start(BATTERY_POLL_MS)
        self.request_battery.emit()

    # --- layout -----------------------------------------------------------

    def _build_header(self):
        row = QHBoxLayout()
        self.device_label = QLabel("Looking for the mouse…")
        self.device_label.setStyleSheet("font-weight: bold;")
        self.battery_bar = QProgressBar()
        self.battery_bar.setRange(0, 100)
        self.battery_bar.setFixedWidth(160)
        self.battery_bar.setFormat("Battery %p%")
        self.battery_bar.setVisible(False)
        self.battery_label = QLabel()
        refresh_icon = QIcon.fromTheme("view-refresh")
        refresh = QPushButton(refresh_icon, "" if not refresh_icon.isNull() else "Refresh")
        refresh.setToolTip("Refresh battery and connection")
        refresh.clicked.connect(self.request_battery.emit)
        row.addWidget(self.device_label)
        row.addStretch()
        row.addWidget(self.battery_label)
        row.addWidget(self.battery_bar)
        row.addWidget(refresh)
        return row

    def _build_dpi_group(self):
        box = QGroupBox("Sensitivity")
        grid = QGridLayout(box)
        grid.addWidget(QLabel("Presets:"), 0, 0)
        self.dpi_count = _spin(1, cfg.MAX_DPI_PRESETS)
        self.dpi_count.valueChanged.connect(self._update_dpi_rows)
        grid.addWidget(self.dpi_count, 0, 1)
        self.dpi_active_group = QButtonGroup(box)
        self.dpi_radios, self.dpi_spins = [], []
        for index in range(cfg.MAX_DPI_PRESETS):
            radio = QRadioButton(f"Preset {index + 1}")
            radio.setToolTip("Active preset (cycle with the button below the wheel)")
            spin = _spin(cfg.DPI_MIN, cfg.DPI_MAX, cfg.DPI_STEP, " DPI")
            spin.setKeyboardTracking(False)
            spin.valueChanged.connect(self._on_form_changed)
            self.dpi_active_group.addButton(radio, index + 1)
            grid.addWidget(radio, index + 1, 0)
            grid.addWidget(spin, index + 1, 1)
            self.dpi_radios.append(radio)
            self.dpi_spins.append(spin)
        self.dpi_active_group.idToggled.connect(self._on_form_changed)
        grid.addWidget(QLabel("Polling rate:"), cfg.MAX_DPI_PRESETS + 1, 0)
        self.polling = QComboBox()
        for rate in cfg.POLLING_RATES:
            self.polling.addItem(f"{rate} Hz", rate)
        self.polling.currentIndexChanged.connect(self._on_form_changed)
        grid.addWidget(self.polling, cfg.MAX_DPI_PRESETS + 1, 1)
        return box

    def _build_power_group(self):
        box = QGroupBox("Power")
        form = QFormLayout(box)
        self.sleep_timer = _spin(0, cfg.SLEEP_TIMER_MAX, suffix=" min", never=True)
        self.sleep_timer.valueChanged.connect(self._on_form_changed)
        self.dim_timer = _spin(0, cfg.DIM_TIMER_MAX, 5, " s", never=True)
        self.dim_timer.valueChanged.connect(self._on_form_changed)
        form.addRow("Sleep after:", self.sleep_timer)
        form.addRow("Dim LEDs after:", self.dim_timer)
        return box

    def _build_lighting_group(self):
        box = QGroupBox("Lighting")
        form = QFormLayout(box)
        self.lighting = QComboBox()
        for key, label in LIGHTING_LABELS.items():
            self.lighting.addItem(label, key)
        self.lighting.currentIndexChanged.connect(self._on_form_changed)
        form.addRow("Effect:", self.lighting)
        self.zone_buttons = {}
        for name, label in (("top_color", "Top:"), ("middle_color", "Middle:"), ("bottom_color", "Bottom:")):
            button = ColorButton()
            button.changed.connect(self._on_form_changed)
            self.zone_buttons[name] = button
            form.addRow(label, button)
        same = QPushButton("Use top color for all zones")
        same.clicked.connect(self._same_color_all_zones)
        form.addRow("", same)

        reactive_row = QHBoxLayout()
        self.reactive_enabled = QCheckBox("Flash on click")
        self.reactive_enabled.toggled.connect(self._on_form_changed)
        self.reactive_color = ColorButton()
        self.reactive_color.changed.connect(self._on_form_changed)
        reactive_row.addWidget(self.reactive_enabled)
        reactive_row.addWidget(self.reactive_color)
        form.addRow("Reactive:", reactive_row)

        self.startup = QComboBox()
        for key, label in STARTUP_LABELS.items():
            self.startup.addItem(label, key)
        self.startup.currentIndexChanged.connect(self._on_form_changed)
        form.addRow("At power-on:", self.startup)
        hint = QLabel(
            "The mouse cannot store colors. After sleep or power-off it shows the "
            "power-on lighting until colors are re-applied. Enable the background "
            "service (see README) to restore them automatically."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(placeholder-text); font-size: small;")
        form.addRow(hint)
        return box

    def _build_profiles_group(self):
        box = QGroupBox("Profiles")
        row = QHBoxLayout(box)
        self.profiles = QComboBox()
        self.profiles.setMinimumWidth(180)
        load = QPushButton("Load")
        load.setToolTip("Load the profile into the form (press Apply to send it)")
        load.clicked.connect(self._load_profile)
        save = QPushButton("Save as…")
        save.clicked.connect(self._save_profile)
        delete = QPushButton("Delete")
        delete.clicked.connect(self._delete_profile)
        self._profile_buttons = (load, delete)
        for widget in (self.profiles, load, save, delete):
            row.addWidget(widget)
        row.addStretch()
        return box

    def _build_buttons_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.manage_buttons = QCheckBox("Manage the button mapping with aerox9ctl")
        self.manage_buttons.setToolTip(
            "Off: the mouse keeps its current mapping (e.g. one made in SteelSeries GG).\n"
            "On: Apply sends the mapping below and replaces the whole mapping on the mouse."
        )
        self.manage_buttons.toggled.connect(self._on_form_changed)
        layout.addWidget(self.manage_buttons)

        self.button_combos = {}
        columns = QHBoxLayout()
        main = QGroupBox("Top buttons and wheel")
        main_form = QFormLayout(main)
        for name in ("button1", "button2", "button3", "dpi_button", "scrollup", "scrolldown", "button4", "button5"):
            main_form.addRow(btn.LABELS[name] + ":", self._button_combo(name))
        columns.addWidget(main)
        side = QGroupBox("Side keypad")
        grid = QGridLayout(side)
        for number in range(1, 13):
            row, column = divmod(number - 1, 3)
            cell = QVBoxLayout()
            cell.setSpacing(2)
            cell.addWidget(QLabel(str(number)))
            cell.addWidget(self._button_combo(f"side{number}"))
            grid.addLayout(cell, row, column)
        columns.addWidget(side, 1)
        layout.addLayout(columns)

        restore = QPushButton("Factory default mapping")
        restore.clicked.connect(self._default_button_mapping)
        hint = QLabel(
            "Type an action, e.g. F13, VolumeUp, disabled, or up to 4 keys joined with '+' "
            "(LeftShift+1). Key names are US-layout positions ('-' = ß, '=' = ´ on German keyboards). "
            "Macros are not supported."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(placeholder-text); font-size: small;")
        row = QHBoxLayout()
        row.addWidget(restore)
        row.addWidget(hint, 1)
        layout.addLayout(row)
        layout.addWidget(self._build_tester_group())
        return tab

    def _button_combo(self, name: str) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItems(btn.all_actions())
        combo.setMinimumContentsLength(9)
        combo.currentTextChanged.connect(self._on_form_changed)
        self.button_combos[name] = combo
        return combo

    def _build_tester_group(self):
        box = QGroupBox("Test buttons")
        layout = QVBoxLayout(box)
        row = QHBoxLayout()
        self.tester_toggle = QPushButton("Start listening")
        self.tester_toggle.setCheckable(True)
        self.tester_toggle.toggled.connect(self._toggle_tester)
        row.addWidget(self.tester_toggle)
        row.addWidget(QLabel("Shows what each button sends right now. Nothing is written to the mouse."), 1)
        layout.addLayout(row)
        self.tester_log = QPlainTextEdit()
        self.tester_log.setReadOnly(True)
        self.tester_log.setMaximumBlockCount(500)
        self.tester_log.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.tester_log.setPlaceholderText("Press Start, then press buttons on the mouse.")
        self.tester_log.setMinimumHeight(110)
        layout.addWidget(self.tester_log)
        self._tester_fds = {}
        self._tester_notifiers = []
        self._tester_tracker = None
        return box

    def _build_action_row(self):
        row = QHBoxLayout()
        defaults = QPushButton("Factory defaults")
        defaults.setToolTip(
            "Fill the General tab with factory defaults (press Apply to send them).\n"
            "The Buttons tab has its own reset."
        )
        defaults.clicked.connect(self._factory_defaults)
        self.revert_button = QPushButton("Revert")
        self.revert_button.setToolTip("Discard unapplied changes")
        self.revert_button.clicked.connect(lambda: self.set_form(self._applied or cfg.MouseConfig()))
        self.save_to_mouse = QCheckBox("Save to mouse memory")
        self.save_to_mouse.setChecked(True)
        self.save_to_mouse.setToolTip("Persist DPI, polling rate, timers and power-on lighting in the mouse")
        self.apply_button = QPushButton("Apply")
        self.apply_button.setDefault(True)
        self.apply_button.clicked.connect(self._apply)
        row.addWidget(defaults)
        row.addWidget(self.revert_button)
        row.addStretch()
        row.addWidget(self.save_to_mouse)
        row.addWidget(self.apply_button)
        return row

    # --- form <-> config --------------------------------------------------

    def set_form(self, config: cfg.MouseConfig):
        self._loading = True
        try:
            self.dpi_count.setValue(len(config.dpi_presets))
            for index, spin in enumerate(self.dpi_spins):
                if index < len(config.dpi_presets):
                    spin.setValue(config.dpi_presets[index])
            self.dpi_radios[config.dpi_active - 1].setChecked(True)
            self.polling.setCurrentIndex(self.polling.findData(config.polling_rate))
            self.lighting.setCurrentIndex(self.lighting.findData(config.lighting))
            for name, button in self.zone_buttons.items():
                button.set_color(getattr(config, name))
            reactive_on = config.reactive_color != "off"
            self.reactive_enabled.setChecked(reactive_on)
            self.reactive_color.set_color(config.reactive_color if reactive_on else "#ffffff")
            self.startup.setCurrentIndex(self.startup.findData(config.startup_lighting))
            self.sleep_timer.setValue(config.sleep_timer)
            self.dim_timer.setValue(config.dim_timer)
            self.manage_buttons.setChecked(config.buttons is not None)
            mapping = dict(config.buttons) if config.buttons is not None else btn.DEFAULTS
            for name, combo in self.button_combos.items():
                combo.setCurrentText(mapping[name])
        finally:
            self._loading = False
        self._update_dpi_rows()

    def config_from_form(self) -> cfg.MouseConfig:
        count = self.dpi_count.value()
        active = self.dpi_active_group.checkedId()
        return cfg.MouseConfig(
            dpi_presets=tuple(spin.value() for spin in self.dpi_spins[:count]),
            dpi_active=active if 1 <= active <= count else 1,
            polling_rate=self.polling.currentData(),
            lighting=self.lighting.currentData(),
            top_color=self.zone_buttons["top_color"].color(),
            middle_color=self.zone_buttons["middle_color"].color(),
            bottom_color=self.zone_buttons["bottom_color"].color(),
            reactive_color=self.reactive_color.color() if self.reactive_enabled.isChecked() else "off",
            startup_lighting=self.startup.currentData(),
            sleep_timer=self.sleep_timer.value(),
            dim_timer=self.dim_timer.value(),
            buttons=(
                {name: combo.currentText() for name, combo in self.button_combos.items()}
                if self.manage_buttons.isChecked()
                else None
            ),
        )

    # --- slots ------------------------------------------------------------

    def _update_dpi_rows(self):
        count = self.dpi_count.value()
        for index, (radio, spin) in enumerate(zip(self.dpi_radios, self.dpi_spins)):
            radio.setEnabled(index < count)
            spin.setEnabled(index < count)
        if self.dpi_active_group.checkedId() > count:
            self.dpi_radios[count - 1].setChecked(True)
        self._on_form_changed()

    def _on_form_changed(self, *_):
        if self._loading:
            return
        is_static = self.lighting.currentData() == "static"
        for button in self.zone_buttons.values():
            button.setEnabled(is_static)
        self.reactive_color.setEnabled(self.reactive_enabled.isChecked())
        for combo in self.button_combos.values():
            combo.setEnabled(self.manage_buttons.isChecked())
        try:
            dirty = self.config_from_form() != self._applied
        except cfg.ConfigError:
            dirty = True
        self.revert_button.setEnabled(dirty and self._applied is not None)
        self.setWindowTitle(("* " if dirty else "") + "Aerox 9 Wireless")

    def _factory_defaults(self):
        self._loading = True  # keep the Buttons tab exactly as it is
        mapping = {name: combo.currentText() for name, combo in self.button_combos.items()}
        managed = self.manage_buttons.isChecked()
        self._loading = False
        self.set_form(cfg.MouseConfig())
        self._loading = True
        self.manage_buttons.setChecked(managed)
        for name, combo in self.button_combos.items():
            combo.setCurrentText(mapping[name])
        self._loading = False
        self._on_form_changed()

    def _default_button_mapping(self):
        for name, combo in self.button_combos.items():
            combo.setCurrentText(btn.DEFAULTS[name])

    def _toggle_tester(self, on: bool):
        if not on:
            self._stop_tester()
            return
        try:
            nodes = tester.find_input_nodes()
            for iface, path in nodes.items():
                self._tester_fds[os.open(path, os.O_RDONLY | os.O_NONBLOCK)] = iface
        except (device.DeviceNotFound, OSError) as error:
            self._stop_tester()
            self.tester_toggle.setChecked(False)
            QMessageBox.warning(self, "Cannot listen to the mouse", str(error))
            return
        self._tester_tracker = tester.ReportTracker()
        for fd in self._tester_fds:
            notifier = QSocketNotifier(fd, QSocketNotifier.Read, self)
            notifier.activated.connect(lambda *_signal_args, fd=fd: self._read_tester(fd))
            self._tester_notifiers.append(notifier)
        self.tester_toggle.setText("Stop listening")
        self.tester_log.appendPlainText("-- listening (the DPI button and disabled buttons produce no output)")

    def _read_tester(self, fd: int):
        if fd not in self._tester_fds:  # already stopped
            return
        try:
            report = os.read(fd, 64)
        except BlockingIOError:
            return
        except OSError as error:  # unplugged
            self.tester_log.appendPlainText(f"-- stopped: {error}")
            self.tester_toggle.setChecked(False)
            return
        for event in self._tester_tracker.feed(self._tester_fds[fd], report):
            self.tester_log.appendPlainText(event)

    def _stop_tester(self):
        for notifier in self._tester_notifiers:
            notifier.setEnabled(False)
            notifier.deleteLater()
        self._tester_notifiers = []
        for fd in self._tester_fds:
            os.close(fd)
        if self._tester_fds:
            self.tester_log.appendPlainText("-- stopped")
        self._tester_fds = {}
        self.tester_toggle.setText("Start listening")

    def _same_color_all_zones(self):
        color = self.zone_buttons["top_color"].color()
        for button in self.zone_buttons.values():
            button.set_color(color)

    def _apply(self):
        try:
            new = self.config_from_form()
        except cfg.ConfigError as error:
            QMessageBox.warning(self, "Invalid settings", str(error))
            return
        # Nothing changed: re-send everything (e.g. after the mouse lost its colors).
        groups = device.changed_groups(self._applied, new) or device.ALL_GROUPS
        self.apply_button.setEnabled(False)
        self.statusBar().showMessage("Applying…")
        self.request_apply.emit(new, groups, self.save_to_mouse.isChecked())

    @Slot(object, str)
    def _on_applied(self, config, message):
        self.apply_button.setEnabled(True)
        if config is None:
            QMessageBox.warning(self, "Apply failed", message)
            self.statusBar().showMessage(message)
            return
        self._applied = config
        self.statusBar().showMessage(message, 5000)
        self._on_form_changed()

    @Slot(object, str, str)
    def _on_battery(self, battery, name, error):
        self.device_label.setText(name or error)
        if battery is None:
            self.battery_bar.setVisible(False)
            self.battery_label.setText("" if error else "Battery unavailable (asleep?)")
            return
        self.battery_bar.setVisible(True)
        self.battery_bar.setValue(battery.level)
        self.battery_label.setText("⚡ charging" if battery.charging else "")

    def _refresh_profiles(self, select: str | None = None):
        self.profiles.clear()
        names = cfg.list_profiles()
        self.profiles.addItems(names)
        if select in names:
            self.profiles.setCurrentText(select)
        for button in self._profile_buttons:
            button.setEnabled(bool(names))

    def _load_profile(self):
        name = self.profiles.currentText()
        try:
            self.set_form(cfg.load_file(cfg.profile_path(name)))
        except cfg.ConfigError as error:
            QMessageBox.warning(self, "Cannot load profile", str(error))
            return
        self.statusBar().showMessage(f"Loaded profile '{name}'. Press Apply to send it to the mouse.", 5000)

    def _save_profile(self):
        name, ok = QInputDialog.getText(self, "Save profile", "Profile name:", text=self.profiles.currentText())
        if not ok or not name.strip():
            return
        name = name.strip()
        try:
            config = self.config_from_form()
            path = cfg.profile_path(name)
        except cfg.ConfigError as error:
            QMessageBox.warning(self, "Cannot save profile", str(error))
            return
        if path.exists() and QMessageBox.question(self, "Overwrite?", f"Overwrite profile '{name}'?") != QMessageBox.Yes:
            return
        cfg.save_file(config, path)
        self._refresh_profiles(select=name)
        self.statusBar().showMessage(f"Saved profile '{name}'.", 5000)

    def _delete_profile(self):
        name = self.profiles.currentText()
        if QMessageBox.question(self, "Delete profile", f"Delete profile '{name}'?") != QMessageBox.Yes:
            return
        cfg.profile_path(name).unlink(missing_ok=True)
        self._refresh_profiles()

    def closeEvent(self, event):
        self._stop_tester()
        self._battery_timer.stop()
        self._worker_thread.quit()
        self._worker_thread.wait(3000)
        super().closeEvent(event)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("aerox9ctl")
    app.setDesktopFileName("aerox9ctl")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
