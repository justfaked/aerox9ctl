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

import ctypes
import os
import select
import sys
import time
from contextlib import contextmanager
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

#: (usage page, usage) of each interface's top-level collection.
INTERFACE_USAGES = {
    IFACE_MOUSE: (0x01, 0x02),
    IFACE_KEYBOARD: (0x01, 0x06),
    IFACE_CONSUMER: (0x0C, 0x01),
    IFACE_BOOT_KEYBOARD: (0x01, 0x06),
}
POLL_SECONDS = 0.01  # sources without a file descriptor are polled this often
INPUT_MONITORING_HINT = (
    "On macOS, allow the app you started aerox9ctl from (e.g. Terminal) under "
    "System Settings > Privacy & Security > Input Monitoring, then restart it."
)


class HidrawSource:
    """An input interface read straight from ``/dev/hidrawN`` (Linux)."""

    def __init__(self, interface: int, path: Path):
        self.interface = interface
        self._fd: int | None = os.open(path, os.O_RDONLY | os.O_NONBLOCK)

    def fileno(self) -> int | None:
        return self._fd

    def read(self) -> bytes:
        """The next report, ``b""`` if none is waiting. Raises OSError when unplugged."""
        try:
            return os.read(self._fd, 64)
        except BlockingIOError:
            return b""

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None


class HidapiSource:
    """An input interface read through hidapi (macOS). It has no file descriptor: poll it."""

    def __init__(self, interface: int, path: bytes, hid_module):
        self.interface = interface
        self._device = hid_module.device()
        self._device.open_path(path)
        self._device.set_nonblocking(1)

    def fileno(self) -> int | None:
        return None

    def read(self) -> bytes:
        return bytes(self._device.read(64))

    def close(self) -> None:
        self._device.close()


def find_input_nodes(sysfs: Path = Path("/sys/class/hidraw")) -> dict[int, Path]:
    """``{interface number: /dev/hidrawN}`` for the connected Aerox 9 (Linux)."""
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
    nodes = {iface: path for iface, path in nodes.items() if iface in INTERFACE_USAGES}
    if not nodes:
        raise DeviceNotFound("no SteelSeries Aerox 9 Wireless found (is the dongle or cable plugged in?)")
    return nodes


def find_hidapi_inputs(hid_module) -> dict[int, bytes]:
    """``{interface number: hidapi path}`` for the connected Aerox 9.

    hidapi lists one entry per top-level collection; prefer the collection
    whose usage matches what the interface is decoded as.
    """
    candidates: dict[int, list[dict]] = {}
    for info in hid_module.enumerate(VENDOR_ID, 0):
        if info["product_id"] in PRODUCT_IDS and info["interface_number"] in INTERFACE_USAGES:
            candidates.setdefault(info["interface_number"], []).append(info)
    if not candidates:
        raise DeviceNotFound("no SteelSeries Aerox 9 Wireless found (is the dongle or cable plugged in?)")
    paths = {}
    for interface, infos in sorted(candidates.items()):
        wanted = INTERFACE_USAGES[interface]
        best = next((i for i in infos if (i.get("usage_page"), i.get("usage")) == wanted), infos[0])
        paths[interface] = best["path"]
    return paths


@contextmanager
def _shared_access(hid_module):
    """Open devices without seizing them; yields False if that is not possible.

    On macOS hidapi opens devices exclusively by default, which would take the
    pointer and the side keys away from the system while listening. The Python
    binding does not expose the switch, but the C function is built into its
    extension module.
    """
    if sys.platform != "darwin":
        yield True
        return
    try:
        library = ctypes.CDLL(hid_module.__file__)
        setter, getter = library.hid_darwin_set_open_exclusive, library.hid_darwin_get_open_exclusive
    except (OSError, AttributeError, TypeError):
        yield False
        return
    previous = getter()
    setter(0)
    try:
        yield True
    finally:
        setter(previous)


def _open_hidapi_inputs() -> list[HidapiSource]:
    import hid

    paths = find_hidapi_inputs(hid)
    sources, failed = [], []
    with _shared_access(hid) as shared:
        if not shared:  # seizing the mouse interface would freeze the pointer
            paths.pop(IFACE_MOUSE, None)
        for interface, path in paths.items():
            try:
                sources.append(HidapiSource(interface, path, hid))
            except OSError:
                failed.append(interface)
    if failed:
        for source in sources:
            source.close()
        hint = f" {INPUT_MONITORING_HINT}" if sys.platform == "darwin" else ""
        names = ", ".join(str(interface) for interface in failed)
        raise OSError(f"cannot open the mouse's input interface(s) {names}.{hint}")
    return sources


def open_inputs() -> list:
    """Open the mouse's input interfaces read-only (hidraw on Linux, hidapi elsewhere)."""
    if not sys.platform.startswith("linux"):
        return _open_hidapi_inputs()
    sources = []
    try:
        for interface, path in find_input_nodes().items():
            sources.append(HidrawSource(interface, path))
    except OSError:
        for source in sources:
            source.close()
        raise
    return sources


def drain(source, tracker: ReportTracker) -> list[str]:
    """Events from every report waiting on ``source``. Raises OSError when unplugged."""
    events = []
    while report := source.read():
        events += tracker.feed(source.interface, report)
    return events


def listen(print_event=print, sources: list | None = None) -> None:
    """Print button events until interrupted (Ctrl+C)."""
    sources = open_inputs() if sources is None else sources
    pollable = all(source.fileno() is not None for source in sources)
    tracker = ReportTracker()
    try:
        while True:
            if pollable:
                ready, _, _ = select.select(sources, [], [])
            else:
                ready = sources
                time.sleep(POLL_SECONDS)
            for source in ready:
                for event in drain(source, tracker):
                    print_event(event)
    finally:
        for source in sources:
            source.close()
