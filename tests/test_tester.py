import pytest

from aerox9ctl import tester
from aerox9ctl.device import DeviceNotFound


def mouse_report(buttons=0, wheel=0, pan=0):
    return bytes([buttons, 0, 0, 0, 0, wheel & 0xFF, pan & 0xFF, 0, 0, 0, 0, 0])


def test_mouse_buttons_and_wheel():
    track = tester.ReportTracker()
    assert track.feed(0, mouse_report(buttons=0b1000)) == ["pressed:  mouse button4"]
    assert track.feed(0, mouse_report(buttons=0b1000)) == []  # movement while held
    assert track.feed(0, mouse_report()) == ["released: mouse button4"]
    assert track.feed(0, mouse_report(wheel=1)) == ["scroll up"]
    assert track.feed(0, mouse_report(wheel=-1)) == ["scroll down"]
    assert track.feed(0, mouse_report(pan=-1)) == ["scroll left"]


def test_key_bitmap():
    report = bytearray(32)
    report[0x1E // 8] |= 1 << (0x1E % 8)  # "1"
    report[0x68 // 8] |= 1 << (0x68 % 8)  # F13
    track = tester.ReportTracker()
    assert track.feed(1, bytes(report)) == ["pressed:  key 1", "pressed:  key F13"]
    assert track.feed(1, bytes(32)) == ["released: key 1", "released: key F13"]


def test_boot_keyboard():
    track = tester.ReportTracker()
    report = bytes([0b0000_0010, 0, 0x2E, 0, 0, 0, 0, 0])  # LeftShift + "="
    assert track.feed(5, report) == ["pressed:  key =", "pressed:  key LeftShift"]


def test_consumer():
    track = tester.ReportTracker()
    assert track.feed(2, bytes([0xE9, 0x00, 0, 0])) == ["pressed:  media VolumeUp"]
    assert track.feed(2, bytes(4)) == ["released: media VolumeUp"]
    assert track.feed(2, bytes([0x23, 0x02, 0, 0])) == ["pressed:  media media 0x0223"]


def test_unknown_interfaces_and_empty_reports_are_ignored():
    track = tester.ReportTracker()
    assert track.feed(3, b"\x01" * 64) == []
    assert track.feed(0, b"") == []


def _fake_hidraw(root, name, hid_id, interface):
    device_dir = root / "devices" / name / "hid"
    device_dir.mkdir(parents=True)
    (device_dir / "uevent").write_text(f"DRIVER=hid-generic\nHID_ID={hid_id}\nHID_NAME=x\n")
    (device_dir.parent / "bInterfaceNumber").write_text(f"{interface:02x}\n")
    link_dir = root / "class"
    link_dir.mkdir(exist_ok=True)
    (link_dir / name).mkdir()
    (link_dir / name / "device").symlink_to(device_dir)


def test_find_input_nodes(tmp_path):
    _fake_hidraw(tmp_path, "hidraw3", "0003:00001038:00001858", 0)
    _fake_hidraw(tmp_path, "hidraw4", "0003:00001038:00001858", 3)  # vendor interface: skipped
    _fake_hidraw(tmp_path, "hidraw5", "0003:00001038:00001858", 1)
    _fake_hidraw(tmp_path, "hidraw6", "0003:00001038:00001B04", 0)  # other SteelSeries device
    nodes = tester.find_input_nodes(tmp_path / "class")
    assert {iface: path.name for iface, path in nodes.items()} == {0: "hidraw3", 1: "hidraw5"}


def test_find_input_nodes_without_mouse(tmp_path):
    (tmp_path / "class").mkdir()
    with pytest.raises(DeviceNotFound):
        tester.find_input_nodes(tmp_path / "class")
