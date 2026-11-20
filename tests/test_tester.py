import sys
import types

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


# --- hidapi (macOS) ---------------------------------------------------------


def _info(interface, usage_page, usage, product_id=0x1858):
    path = f"IOService:/{product_id:04x}/{interface}/{usage_page:x}:{usage:x}".encode()
    return {"product_id": product_id, "interface_number": interface, "usage_page": usage_page,
            "usage": usage, "path": path}


ENUMERATION = [
    _info(0, 0x01, 0x01),  # pointer collection: the mouse collection is preferred
    _info(0, 0x01, 0x02),
    _info(1, 0x01, 0x06),
    _info(2, 0x0C, 0x01),
    _info(3, 0xFFC0, 0x01),  # vendor interface: never opened by the tester
    _info(5, 0x01, 0x06),
    _info(0, 0x01, 0x02, product_id=0x1B04),  # another SteelSeries device
]


def fake_hid(enumeration=ENUMERATION, failing=(), reports=None):
    """A stand-in for hidapi's ``hid`` module."""
    module = types.ModuleType("hid")
    module.opened, module.closed = [], []
    reports = reports or {}

    class Device:
        def open_path(self, path):
            if any(path == info["path"] for info in enumeration if info["interface_number"] in failing):
                raise OSError("open failed")
            self.path = path
            module.opened.append(path)

        def set_nonblocking(self, value):
            assert value == 1

        def read(self, size):
            queue = reports.get(self.path, [])
            return list(queue.pop(0)) if queue else []

        def close(self):
            module.closed.append(self.path)

    module.device = Device
    module.enumerate = lambda vendor_id=0, product_id=0: [
        info for info in enumeration if vendor_id in (0, 0x1038) and product_id in (0, info["product_id"])
    ]
    return module


def test_find_hidapi_inputs_prefers_matching_collection():
    paths = tester.find_hidapi_inputs(fake_hid())
    assert paths == {
        0: ENUMERATION[1]["path"], 1: ENUMERATION[2]["path"], 2: ENUMERATION[3]["path"], 5: ENUMERATION[5]["path"]
    }


def test_find_hidapi_inputs_without_mouse():
    with pytest.raises(DeviceNotFound):
        tester.find_hidapi_inputs(fake_hid(enumeration=[_info(0, 1, 2, product_id=0x1B04)]))


class FakeLibrary:
    def __init__(self):
        self.exclusive = 1
        self.during_open = []

    def hid_darwin_set_open_exclusive(self, value):
        self.exclusive = value

    def hid_darwin_get_open_exclusive(self):
        return self.exclusive


def test_macos_opens_inputs_shared_and_restores_exclusive_mode(monkeypatch):
    hid = fake_hid()
    library = FakeLibrary()
    original_device = hid.device

    class Device(original_device):
        def open_path(self, path):
            library.during_open.append(library.exclusive)
            super().open_path(path)

    hid.device = Device
    hid.__file__ = "/fake/hid.so"
    monkeypatch.setitem(sys.modules, "hid", hid)
    monkeypatch.setattr(tester.sys, "platform", "darwin")
    monkeypatch.setattr(tester.ctypes, "CDLL", lambda path: library)
    sources = tester.open_inputs()
    assert sorted(source.interface for source in sources) == [0, 1, 2, 5]
    assert all(source.fileno() is None for source in sources)
    assert library.during_open == [0, 0, 0, 0]
    assert library.exclusive == 1


def test_macos_skips_mouse_interface_when_it_would_be_seized(monkeypatch):
    hid = fake_hid()  # no __file__: the exclusive switch cannot be found
    monkeypatch.setitem(sys.modules, "hid", hid)
    monkeypatch.setattr(tester.sys, "platform", "darwin")
    sources = tester.open_inputs()
    assert sorted(source.interface for source in sources) == [1, 2, 5]


def test_macos_open_failure_mentions_input_monitoring(monkeypatch):
    hid = fake_hid(failing={1, 5})
    monkeypatch.setitem(sys.modules, "hid", hid)
    monkeypatch.setattr(tester.sys, "platform", "darwin")
    with pytest.raises(OSError, match=r"interface\(s\) 1, 5\..*Input Monitoring"):
        tester.open_inputs()
    assert sorted(hid.closed) == sorted(hid.opened)  # the ones that did open are closed again


def test_listen_polls_sources_without_fd(monkeypatch):
    path = ENUMERATION[1]["path"]
    hid = fake_hid(reports={path: [bytes([0b1000] + [0] * 11), bytes(12)]})
    source = tester.HidapiSource(0, path, hid)
    monkeypatch.setattr(tester, "POLL_SECONDS", 0)
    events = []

    def print_event(event):
        events.append(event)
        if len(events) == 2:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        tester.listen(print_event, sources=[source])
    assert events == ["pressed:  mouse button4", "released: mouse button4"]
    assert hid.closed == [path]


def test_listen_selects_on_hidraw_nodes(tmp_path, monkeypatch):
    import os

    fifo = tmp_path / "hidraw-fake"
    os.mkfifo(fifo)
    source = tester.HidrawSource(5, fifo)
    writer = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
    os.write(writer, bytes([0, 0, 0x68, 0, 0, 0, 0, 0]))  # F13
    events = []

    def print_event(event):
        events.append(event)
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        tester.listen(print_event, sources=[source])
    os.close(writer)
    assert events == ["pressed:  key F13"]
    assert source.fileno() is None  # closed
