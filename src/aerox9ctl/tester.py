"""Show what each button of the mouse actually sends.

The mouse cannot report its stored button mapping, so this listens to its
input interfaces instead (read-only, nothing is sent to the mouse). Reports
are decoded from the interfaces' HID report descriptors:

* interface 0: mouse - 8 button bits, X/Y, wheel, horizontal wheel
* interface 1: keyboard - 256-bit key bitmap (N-key rollover)
* interface 2: consumer control - two 16-bit usage codes (media keys)
* interface 5: boot keyboard - modifier bits + 6 key codes
"""

from __future__ import annotations

import os
import select
from pathlib import Path

from rivalcfg.handlers.buttons import layout_multimedia, layout_qwerty

from .device import PRODUCT_IDS, VENDOR_ID, DeviceNotFound

IFACE_MOUSE, IFACE_KEYBOARD, IFACE_CONSUMER, IFACE_BOOT_KEYBOARD = 0, 1, 2, 5

_KEY_NAMES = {}
for _name, _code in layout_qwerty.layout.items():
    _KEY_NAMES.setdefault(_code, _name)
_MEDIA_NAMES = {}
for _name, _code in layout_multimedia.layout.items():
    _MEDIA_NAMES.setdefault(_code, _name)
del _name, _code


def key_name(usage: int) -> str:
    return _KEY_NAMES.get(usage, f"key 0x{usage:02x}")


def media_name(usage: int) -> str:
    return _MEDIA_NAMES.get(usage, f"media 0x{usage:04x}")


# --- report decoders (pure) -------------------------------------------------


def decode_mouse(report: bytes) -> tuple[frozenset[str], int, int]:
    """(pressed buttons, wheel, horizontal wheel). Wheel > 0 is scroll up."""
    pressed = frozenset(f"button{bit + 1}" for bit in range(8) if report[0] & (1 << bit))
    wheel = int.from_bytes(report[5:6], "little", signed=True) if len(report) > 5 else 0
    pan = int.from_bytes(report[6:7], "little", signed=True) if len(report) > 6 else 0
    return pressed, wheel, pan


def decode_key_bitmap(report: bytes) -> frozenset[str]:
    return frozenset(
        key_name(index * 8 + bit)
        for index, byte in enumerate(report[:32])
        for bit in range(8)
        if byte & (1 << bit)
    )


def decode_boot_keyboard(report: bytes) -> frozenset[str]:
    pressed = {key_name(0xE0 + bit) for bit in range(8) if report[0] & (1 << bit)}
    pressed |= {key_name(code) for code in report[2:8] if code > 1}  # 1 = rollover error
    return frozenset(pressed)


def decode_consumer(report: bytes) -> frozenset[str]:
    codes = (int.from_bytes(report[i : i + 2], "little") for i in range(0, min(len(report), 4), 2))
    return frozenset(media_name(code) for code in codes if code)


_DECODERS = {
    IFACE_KEYBOARD: ("key", decode_key_bitmap),
    IFACE_CONSUMER: ("media", decode_consumer),
    IFACE_BOOT_KEYBOARD: ("key", decode_boot_keyboard),
}


class ReportTracker:
    """Turns successive reports into human readable press/release events."""

    def __init__(self):
        self._pressed: dict[int, frozenset[str]] = {}

    def feed(self, interface: int, report: bytes) -> list[str]:
        if not report:
            return []
        events = []
        if interface == IFACE_MOUSE:
            pressed, wheel, pan = decode_mouse(report)
            kind = "mouse"
            if wheel:
                events.append("scroll up" if wheel > 0 else "scroll down")
            if pan:
                events.append("scroll right" if pan > 0 else "scroll left")
        elif interface in _DECODERS:
            kind, decoder = _DECODERS[interface]
            pressed = decoder(report)
        else:
            return []
        before = self._pressed.get(interface, frozenset())
        events += [f"pressed:  {kind} {name}" for name in sorted(pressed - before)]
        events += [f"released: {kind} {name}" for name in sorted(before - pressed)]
        self._pressed[interface] = pressed
        return events


# --- device access ----------------------------------------------------------


def find_input_nodes(sysfs: Path = Path("/sys/class/hidraw")) -> dict[int, Path]:
    """``{interface number: /dev/hidrawN}`` for the connected Aerox 9."""
    nodes = {}
    for entry in sorted(sysfs.glob("hidraw*")):
        try:
            uevent = (entry / "device" / "uevent").read_text()
            interface = int((entry / "device" / ".." / "bInterfaceNumber").read_text(), 16)
        except (OSError, ValueError):
            continue
        ids = dict(line.split("=", 1) for line in uevent.splitlines() if "=" in line).get("HID_ID", "")
        try:
            _bus, vendor, product = (int(part, 16) for part in ids.split(":"))
        except ValueError:
            continue
        if vendor == VENDOR_ID and product in PRODUCT_IDS:
            nodes.setdefault(interface, Path("/dev") / entry.name)
    wanted = {IFACE_MOUSE, IFACE_KEYBOARD, IFACE_CONSUMER, IFACE_BOOT_KEYBOARD}
    nodes = {iface: path for iface, path in nodes.items() if iface in wanted}
    if not nodes:
        raise DeviceNotFound("no SteelSeries Aerox 9 Wireless found (is the dongle or cable plugged in?)")
    return nodes


def listen(print_event=print, nodes: dict[int, Path] | None = None) -> None:
    """Print button events until interrupted (Ctrl+C)."""
    nodes = nodes or find_input_nodes()
    fds = {os.open(path, os.O_RDONLY | os.O_NONBLOCK): iface for iface, path in nodes.items()}
    tracker = ReportTracker()
    try:
        while True:
            ready, _, _ = select.select(list(fds), [], [])
            for fd in ready:
                try:
                    report = os.read(fd, 64)
                except BlockingIOError:
                    continue
                for event in tracker.feed(fds[fd], report):
                    print_event(event)
    finally:
        for fd in fds:
            os.close(fd)
