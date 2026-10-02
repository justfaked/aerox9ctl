"""Button mapping for the Aerox 9 Wireless.

rivalcfg 4.17 cannot remap this mouse's buttons yet. The packet layout comes
from the (not yet merged) rivalcfg pull request #243: one 5-byte field per
button, 20 buttons, sent as three ``0x2A`` packets prefixed with the part
number 0, 1 and 2.

Each field is ``<type> <param> <param> <param> <param>``. For keyboard keys
(type 0x51) the params are up to 4 HID key codes pressed together, modifiers
included (e.g. LeftShift+1 = ``51 E1 1E 00 00``), as observed by the rivalcfg
maintainer on a related mouse (rivalcfg issue #171) and confirmed on the
Aerox 9 over the 2.4 GHz dongle with the button tester. rivalcfg itself only
encodes single keys, so the fields are encoded here; its layouts provide the
key codes.
"""

from __future__ import annotations

import re

from rivalcfg.handlers.buttons import layout_multimedia, layout_qwerty

# (name, label, offset in the mapping packet, default action)
_BUTTON_TABLE = (
    ("button1", "Left button", 0, "button1"),
    ("button2", "Right button", 5, "button2"),
    ("button3", "Wheel click", 10, "button3"),
    ("button4", "Button 4", 15, "button4"),
    ("button5", "Button 5", 20, "button5"),
    ("dpi_button", "DPI button", 25, "dpi"),
    # Side keypad: the device orders the fields by column (3, 6, 9, 12, 2, ...)
    ("side3", "Side 3", 30, "3"),
    ("side6", "Side 6", 35, "6"),
    ("side9", "Side 9", 40, "9"),
    ("side12", "Side 12", 45, "="),
    ("side2", "Side 2", 50, "2"),
    ("side5", "Side 5", 55, "5"),
    ("side8", "Side 8", 60, "8"),
    ("side11", "Side 11", 65, "-"),
    ("side1", "Side 1", 70, "1"),
    ("side4", "Side 4", 75, "4"),
    ("side7", "Side 7", 80, "7"),
    ("side10", "Side 10", 85, "0"),
    ("scrollup", "Scroll up", 90, "scrollup"),
    ("scrolldown", "Scroll down", 95, "scrolldown"),
)

#: Button names in display order (side buttons by their printed number)
BUTTON_NAMES = (
    ["button1", "button2", "button3", "button4", "button5", "dpi_button"]
    + [f"side{n}" for n in range(1, 13)]
    + ["scrollup", "scrolldown"]
)
LABELS = {name: label for name, label, _, _ in _BUTTON_TABLE}
DEFAULTS = {name: default for name, _, _, default in _BUTTON_TABLE}
_OFFSETS = {name: offset for name, _, offset, _ in _BUTTON_TABLE}

_COMMAND = 0x2A
_SPLIT_AT = (30, 60)
_FIELD_LENGTH = 5
MAX_COMBO_KEYS = 4

_TYPE_KEYBOARD = 0x51
_TYPE_MEDIA = 0x61
_FIXED_FIELDS = {
    "button1": 0x01,
    "button2": 0x02,
    "button3": 0x03,
    "button4": 0x04,
    "button5": 0x05,
    "dpi": 0x30,
    "scrollup": 0x31,
    "scrolldown": 0x32,
    "disabled": 0x00,
}

MOUSE_ACTIONS = ("button1", "button2", "button3", "button4", "button5")
SPECIAL_ACTIONS = ("dpi", "scrollup", "scrolldown", "disabled")
MEDIA_KEYS = tuple(layout_multimedia.layout)
KEYBOARD_KEYS = tuple(layout_qwerty.layout)
MODIFIER_KEYS = ("LeftCtrl", "LeftShift", "LeftAlt", "LeftSuper", "RightCtrl", "RightShift", "RightAlt", "RightSuper")


def _canonical_names(layout, extra_aliases=()) -> dict[str, str]:
    names = {key.lower(): key for key in layout.layout}
    for alias, target in (*layout.aliases.items(), *extra_aliases):
        names.setdefault(alias.lower(), target)
    return names


_KEYS = _canonical_names(
    layout_qwerty,
    (("shift", "LeftShift"), ("ctrl", "LeftCtrl"), ("control", "LeftCtrl"), ("rctrl", "RightCtrl"),
     ("rshift", "RightShift"), ("ralt", "RightAlt"), ("altgr", "RightAlt")),
)
_MEDIA = _canonical_names(layout_multimedia)
_SIMPLE = {name.lower(): name for name in (*MOUSE_ACTIONS, *SPECIAL_ACTIONS)}
_SIMPLE["disable"] = "disabled"

# Split "LeftShift+1" on '+', but keep a trailing '+' as part of the key ("Keypad+").
_COMBO_SPLIT = re.compile(r"\+(?=.)")


def all_actions() -> tuple[str, ...]:
    """Every single-key action name, in a sensible order for pickers."""
    return MOUSE_ACTIONS + SPECIAL_ACTIONS + MEDIA_KEYS + KEYBOARD_KEYS


def normalize_action(action: str) -> str:
    """Canonical spelling of an action, or ValueError.

    ``"VOLUP"`` -> ``"VolumeUp"``, ``"shift+1"`` -> ``"LeftShift+1"``.
    """
    if not isinstance(action, str) or not action.strip():
        raise ValueError(f"unknown action {action!r} (see: aerox9ctl buttons actions)")
    text = action.strip()
    lowered = text.lower()
    if lowered in _SIMPLE:
        return _SIMPLE[lowered]
    if lowered in _MEDIA:
        return _MEDIA[lowered]
    if lowered in _KEYS:
        return _KEYS[lowered]
    parts = [part.strip() for part in _COMBO_SPLIT.split(text)]
    if len(parts) < 2:
        raise ValueError(f"unknown action {action!r} (see: aerox9ctl buttons actions)")
    keys = []
    for part in parts:
        if part.lower() not in _KEYS:
            raise ValueError(
                f"unknown key {part!r} in {action!r} (combinations can only contain keyboard keys)"
            )
        keys.append(_KEYS[part.lower()])
    if len(keys) > MAX_COMBO_KEYS:
        raise ValueError(f"{action!r}: at most {MAX_COMBO_KEYS} keys can be pressed together")
    if len(set(keys)) != len(keys):
        raise ValueError(f"{action!r}: the same key appears twice")
    return "+".join(keys)


def normalize_mapping(mapping) -> tuple[tuple[str, str], ...]:
    """Full mapping as ``((button, action), ...)`` in BUTTON_NAMES order.

    Buttons not in ``mapping``, or mapped to ``"default"``, get their factory default.
    """
    items = dict(mapping)
    unknown = sorted(set(items) - set(BUTTON_NAMES))
    if unknown:
        raise ValueError(f"unknown button(s): {', '.join(unknown)} (valid: {', '.join(BUTTON_NAMES)})")
    full = dict(DEFAULTS)
    for name, action in items.items():
        if isinstance(action, str) and action.strip().lower() == "default":
            continue
        full[name] = normalize_action(action)
    return tuple((name, full[name]) for name in BUTTON_NAMES)


def encode_action(action: str) -> list[int]:
    """The 5-byte field for one (normalized or not) action."""
    action = normalize_action(action)
    if action in _FIXED_FIELDS:
        field = [_FIXED_FIELDS[action]]
    elif action.lower() in _MEDIA:
        field = [_TYPE_MEDIA, layout_multimedia.layout[action]]
    else:
        field = [_TYPE_KEYBOARD] + [layout_qwerty.layout[key] for key in _COMBO_SPLIT.split(action)]
    return field + [0x00] * (_FIELD_LENGTH - len(field))


def encode(mapping: dict[str, str]) -> list[list[int]]:
    """The three packet payloads (after the command byte) for a full mapping."""
    full = dict(normalize_mapping(mapping))
    packet = [0x00] * (_FIELD_LENGTH * len(_BUTTON_TABLE))
    for name, action in full.items():
        offset = _OFFSETS[name]
        packet[offset : offset + _FIELD_LENGTH] = encode_action(action)
    bounds = (0, *_SPLIT_AT, len(packet))
    return [[part] + packet[start:end] for part, (start, end) in enumerate(zip(bounds, bounds[1:]))]


def write(mouse, mapping: dict[str, str], *, wireless: bool) -> None:
    """Send a full mapping to an open rivalcfg mouse."""
    command = _COMMAND | (0x40 if wireless else 0x00)
    for payload in encode(mapping):
        mouse._hid_write(data=[command] + payload)
        if wireless:
            # Wireless commands are acknowledged; consume the answer like rivalcfg does
            mouse._hid_device.read(64, timeout_ms=200)
