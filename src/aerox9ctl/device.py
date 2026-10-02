"""Talking to the mouse, via rivalcfg.

rivalcfg knows the Aerox 9 protocol; this module adds:

* the hidraw backend instead of hidapi's libusb default, so the kernel driver
  is never detached and plain ``/dev/hidraw*`` permissions are enough (which
  also makes it work unchanged inside distrobox),
* a lock so the GUI, the CLI and the daemon never interleave commands,
* translation of a :class:`MouseConfig` into rivalcfg setting calls.
"""

from __future__ import annotations

import fcntl
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import rivalcfg.devices
import rivalcfg.mouse
import rivalcfg.usbhid

from . import buttons as btn
from .config import MouseConfig

if os.environ.get("AEROX9CTL_HID_BACKEND", "hidraw") == "hidraw":
    try:
        import hidraw

        rivalcfg.usbhid.hid = hidraw
    except ImportError:  # hidapi built without the hidraw backend: keep libusb
        pass

VENDOR_ID = 0x1038
PRODUCT_IDS = {
    0x1858: "2.4 GHz",
    0x185A: "wired",
    0x1874: "2.4 GHz, WOW Edition",
    0x1876: "wired, WOW Edition",
}
WIRELESS_PRODUCT_IDS = frozenset({0x1858, 0x1874})

# Setting groups. Changing any field of a group re-sends the whole group.
GROUP_DPI = "dpi"
GROUP_POLLING = "polling"
GROUP_POWER = "power"
GROUP_STARTUP = "startup"
GROUP_LIGHTING = "lighting"  # live only: not stored in the mouse's memory
GROUP_BUTTONS = "buttons"  # only sent when the config has a mapping
ALL_GROUPS = frozenset({GROUP_DPI, GROUP_POLLING, GROUP_POWER, GROUP_STARTUP, GROUP_LIGHTING, GROUP_BUTTONS})

FIELD_GROUPS = {
    "dpi_presets": GROUP_DPI,
    "dpi_active": GROUP_DPI,
    "polling_rate": GROUP_POLLING,
    "sleep_timer": GROUP_POWER,
    "dim_timer": GROUP_POWER,
    "startup_lighting": GROUP_STARTUP,
    "lighting": GROUP_LIGHTING,
    "top_color": GROUP_LIGHTING,
    "middle_color": GROUP_LIGHTING,
    "bottom_color": GROUP_LIGHTING,
    "reactive_color": GROUP_LIGHTING,
    "buttons": GROUP_BUTTONS,
}


class DeviceNotFound(Exception):
    """No Aerox 9 Wireless (wired or 2.4 GHz dongle) is connected."""


@dataclass(frozen=True)
class Battery:
    level: int  # percent
    charging: bool


def changed_groups(old: MouseConfig | None, new: MouseConfig) -> frozenset[str]:
    """Groups whose settings differ between ``old`` and ``new`` (all if ``old`` is unknown)."""
    if old is None:
        return ALL_GROUPS
    return frozenset(
        group for name, group in FIELD_GROUPS.items() if getattr(old, name) != getattr(new, name)
    )


def plan_commands(config: MouseConfig, groups=ALL_GROUPS) -> list[tuple[str, tuple]]:
    """The calls that apply ``groups`` of ``config``, in order.

    Each is a rivalcfg ``set_<name>(*args)`` call, except ``"buttons"`` which
    is sent by :func:`aerox9ctl.buttons.write`.
    """
    groups = set(groups)
    if GROUP_STARTUP in groups:
        # The startup setting may affect the LEDs right away: restore live lighting afterwards.
        groups.add(GROUP_LIGHTING)
    calls = []
    if GROUP_DPI in groups:
        calls.append(("sensitivity", (list(config.dpi_presets), config.dpi_active - 1)))
    if GROUP_POLLING in groups:
        calls.append(("polling_rate", (config.polling_rate,)))
    if GROUP_POWER in groups:
        calls.append(("sleep_timer", (config.sleep_timer,)))
        calls.append(("dim_timer", (config.dim_timer,)))
    if GROUP_STARTUP in groups:
        calls.append(("default_lighting", (config.startup_lighting,)))
    if GROUP_LIGHTING in groups:
        # Colors are always sent so they are in place when switching back from rainbow.
        calls.append(("z1_color", (config.top_color,)))
        calls.append(("z2_color", (config.middle_color,)))
        calls.append(("z3_color", (config.bottom_color,)))
        calls.append(("reactive_color", (config.reactive_color,)))
        # Last: setting any color cancels the rainbow effect.
        if config.lighting == "rainbow":
            calls.append(("rainbow_effect", ()))
    if GROUP_BUTTONS in groups and config.buttons is not None:
        calls.append(("buttons", (dict(config.buttons),)))
    return calls


def _lock_path() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return Path(base) / "aerox9ctl.lock"


@contextmanager
def _device_lock():
    with open(_lock_path(), "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _find_product_id() -> int:
    for device in rivalcfg.devices.list_plugged_devices():
        if device["vendor_id"] == VENDOR_ID and device["product_id"] in PRODUCT_IDS:
            return device["product_id"]
    raise DeviceNotFound("no SteelSeries Aerox 9 Wireless found (is the dongle or cable plugged in?)")


@contextmanager
def open_mouse():
    """Open the mouse exclusively for the duration of the ``with`` block.

    :raises DeviceNotFound: no Aerox 9 is connected.
    :raises OSError: the HID device could not be opened (usually permissions).
    """
    with _device_lock():
        product_id = _find_product_id()
        mouse = rivalcfg.mouse.get_mouse(VENDOR_ID, product_id)
        try:
            yield mouse
        finally:
            mouse.close()


def describe() -> str:
    """Human readable name of the connected mouse and its connection mode."""
    product_id = _find_product_id()
    return f"SteelSeries Aerox 9 Wireless ({PRODUCT_IDS[product_id]})"


def apply(config: MouseConfig, groups=ALL_GROUPS, *, save: bool = True) -> None:
    """Send ``groups`` of ``config`` to the mouse.

    With ``save``, persistent settings (DPI, polling rate, timers, startup
    lighting, buttons) are also written to the mouse's onboard memory.
    """
    with open_mouse() as mouse:
        for name, args in plan_commands(config, groups):
            if name == "buttons":
                btn.write(mouse, *args, wireless=mouse.product_id in WIRELESS_PRODUCT_IDS)
            else:
                getattr(mouse, f"set_{name}")(*args)
        if save:
            mouse.save()


def read_battery() -> Battery | None:
    """Battery state, or ``None`` when the mouse does not answer (asleep or off)."""
    with open_mouse() as mouse:
        info = mouse.battery
    if info["level"] is None:
        return None
    return Battery(level=info["level"], charging=bool(info["is_charging"]))
