import pytest

from aerox9ctl import buttons as btn
from aerox9ctl import config as cfg
from aerox9ctl import device
from aerox9ctl.cli import main
from aerox9ctl.config import ConfigError, MouseConfig
from conftest import packets


def field(*data):
    return " ".join(f"{b:02x}" for b in data) + " 00" * (5 - len(data))


DEFAULT_PACKETS = (
    # part 0: buttons 1-5, DPI button (0x30 = DPI cycle)
    "00 " + " ".join(field(i) for i in (1, 2, 3, 4, 5)) + " " + field(0x30),
    # part 1: side 3, 6, 9, 12, 2, 5 -> keys 3 6 9 = 2 5 (0x51 = keyboard key)
    "01 " + " ".join(field(0x51, code) for code in (0x20, 0x23, 0x26, 0x2E, 0x1F, 0x22)),
    # part 2: side 8, 11, 1, 4, 7, 10 -> keys 8 - 1 4 7 0, then scroll up/down
    "02 " + " ".join(field(0x51, code) for code in (0x25, 0x2D, 0x1E, 0x21, 0x24, 0x27))
    + " " + field(0x31) + " " + field(0x32),
)


def test_default_mapping_encoding():
    parts = btn.encode(btn.DEFAULTS)
    assert [bytes(part) for part in parts] == [bytes.fromhex(p) for p in DEFAULT_PACKETS]
    assert [len(part) for part in parts] == [31, 31, 41]


def test_mapping_covers_every_button_once():
    assert sorted(btn.BUTTON_NAMES) == sorted(btn.DEFAULTS)
    assert len(btn.BUTTON_NAMES) == 20


@pytest.mark.parametrize(
    "action, expected",
    [("f13", "F13"), ("VOLUP", "VolumeUp"), ("disable", "disabled"), ("esc", "Escape"),
     ("Button4", "button4"), ("=", "=")],
)
def test_action_normalization(action, expected):
    assert btn.normalize_action(action) == expected


@pytest.mark.parametrize("action", ["macro1", "", "side3", "button6"])
def test_unknown_actions(action):
    with pytest.raises(ValueError, match="unknown action"):
        btn.normalize_action(action)


def test_partial_mapping_and_default_keyword():
    mapping = dict(btn.normalize_mapping({"side1": "F13", "side2": "default"}))
    assert mapping["side1"] == "F13"
    assert mapping["side2"] == "2"
    assert mapping["button1"] == "button1"


def test_media_and_disabled_encoding():
    mapping = dict(btn.DEFAULTS, side3="VolumeUp", button4="disabled")
    part0, part1, _ = btn.encode(mapping)
    assert part0[1 + 15] == 0x00  # button4 field (offset 15) disabled
    assert part1[1:3] == [0x61, 0xE9]  # side3 is the first field of part 1


def test_config_rejects_bad_buttons():
    with pytest.raises(ConfigError, match="buttons: unknown button"):
        MouseConfig(buttons={"side13": "a"})
    with pytest.raises(ConfigError, match="buttons: unknown action"):
        MouseConfig(buttons={"side1": "macro1"})
    with pytest.raises(ConfigError, match="buttons must be a table"):
        MouseConfig(buttons="side1=a")


def test_buttons_are_not_sent_unless_configured():
    calls = [name for name, _ in device.plan_commands(MouseConfig())]
    assert "buttons" not in calls
    calls = [name for name, _ in device.plan_commands(MouseConfig(buttons={}))]
    assert calls[-1] == "buttons"


def test_wireless_wire_format(written):
    device.apply(MouseConfig(buttons={}), {device.GROUP_BUTTONS})
    # 0x2A | 0x40 wireless flag, three parts, then save
    assert written == [packets(*("6a " + p for p in DEFAULT_PACKETS), "51 00")]


def test_wired_wire_format(written, monkeypatch):
    monkeypatch.setenv("RIVALCFG_PROFILE", "1038:185a")
    device.apply(MouseConfig(buttons={}), {device.GROUP_BUTTONS}, save=False)
    assert written == [packets(*("2a " + p for p in DEFAULT_PACKETS))]


def test_cli_set_merges_onto_previous_mapping(written, capsys):
    assert main(["buttons", "set", "side1=F13"]) == 0
    assert main(["buttons", "set", "side2=volup", "--no-save"]) == 0
    mapping = dict(cfg.load_state().buttons)
    assert (mapping["side1"], mapping["side2"]) == ("F13", "VolumeUp")
    # Only the three button packets are sent: no DPI/lighting/etc., no save
    second = written[1]
    assert second.count(bytes.fromhex("02 00 6a")) == 3 and len(second) == 3 * 3 + 31 + 31 + 41


def test_cli_set_keeps_other_settings(written):
    cfg.save_state(MouseConfig(polling_rate=250))
    main(["buttons", "set", "side1=a"])
    state = cfg.load_state()
    assert state.polling_rate == 250
    assert dict(state.buttons)["side1"] == "A"


@pytest.mark.parametrize(
    "argv, message",
    [(["buttons", "set"], "at least one"), (["buttons", "set", "side1"], "BUTTON=ACTION"),
     (["buttons", "set", "side99=a"], "unknown button"), (["buttons", "set", "side1=macro1"], "unknown action")],
)
def test_cli_set_errors(argv, message, written, capsys):
    assert main(argv) == 1
    assert message in capsys.readouterr().err
    assert written == []


def test_cli_reset_and_show(capsys):
    main(["buttons", "show"])
    assert "not managed" in capsys.readouterr().out
    main(["buttons", "set", "side1=F13"])
    capsys.readouterr()
    main(["buttons", "show"])
    out = capsys.readouterr().out
    assert "side1" in out and "F13   (changed)" in out
    main(["buttons", "reset"])
    assert dict(cfg.load_state().buttons) == btn.DEFAULTS


def test_cli_actions(capsys):
    assert main(["buttons", "actions"]) == 0
    out = capsys.readouterr().out
    assert "VolumeUp" in out and "F24" in out


@pytest.mark.parametrize(
    "action, expected",
    [("shift+1", "LeftShift+1"), ("LCTRL + c", "LeftCtrl+C"), ("ctrl+rshift+c", "LeftCtrl+RightShift+C"),
     ("LeftCtrl+Keypad+", "LeftCtrl+Keypad+"), ("Keypad+", "Keypad+"), ("alt+tab", "LeftAlt+Tab")],
)
def test_combo_normalization(action, expected):
    assert btn.normalize_action(action) == expected


@pytest.mark.parametrize(
    "action, message",
    [("shift+volup", "only contain keyboard keys"), ("a+b+c+d+e", "at most 4"),
     ("shift+shift", "twice"), ("shift+", "unknown action"), ("+", "unknown")],
)
def test_bad_combos(action, message):
    with pytest.raises(ValueError, match=message):
        btn.normalize_action(action)


def test_combo_encoding_matches_observed_format():
    # Captures by the rivalcfg maintainer (issue #171): LCtrl+C, LCtrl+RShift+C
    assert btn.encode_action("LeftCtrl+C") == [0x51, 0xE0, 0x06, 0x00, 0x00]
    assert btn.encode_action("LeftCtrl+RightShift+C") == [0x51, 0xE0, 0xE5, 0x06, 0x00]
    assert btn.encode_action("a+b+c+d") == [0x51, 0x04, 0x05, 0x06, 0x07]


def test_shifted_side_keys_encoding():
    mapping = {f"side{n}": f"shift+{key}" for n, key in enumerate("1234567890-=", start=1)}
    _, part1, part2 = btn.encode(mapping)
    assert part1[1:6] == [0x51, 0xE1, 0x20, 0x00, 0x00]  # side3 = Shift+3
    assert part2[11:16] == [0x51, 0xE1, 0x1E, 0x00, 0x00]  # side1 = Shift+1


def test_cli_accepts_combos(written):
    assert main(["buttons", "set", "side1=shift+1"]) == 0
    assert dict(cfg.load_state().buttons)["side1"] == "LeftShift+1"
