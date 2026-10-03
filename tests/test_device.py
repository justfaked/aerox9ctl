import pytest
import rivalcfg.devices
import rivalcfg.mouse

from aerox9ctl import device
from aerox9ctl.config import MouseConfig
from conftest import packets


def test_changed_groups():
    old = MouseConfig()
    assert device.changed_groups(None, old) == device.ALL_GROUPS
    assert device.changed_groups(old, old) == frozenset()
    new = old.merged(polling_rate=500, top_color="blue", dim_timer=10)
    assert device.changed_groups(old, new) == {"polling", "lighting", "power"}


def test_rainbow_is_sent_after_colors():
    calls = [name for name, _ in device.plan_commands(MouseConfig(lighting="rainbow"), {"lighting"})]
    assert calls == ["z1_color", "z2_color", "z3_color", "reactive_color", "rainbow_effect"]
    calls = [name for name, _ in device.plan_commands(MouseConfig(lighting="static"), {"lighting"})]
    assert "rainbow_effect" not in calls


def test_startup_lighting_also_restores_live_lighting():
    calls = [name for name, _ in device.plan_commands(MouseConfig(), {"startup"})]
    assert calls[0] == "default_lighting"
    assert "z1_color" in calls


def test_dpi_active_is_sent_zero_based():
    calls = device.plan_commands(MouseConfig(dpi_presets=[400, 800], dpi_active=2), {"dpi"})
    assert calls == [("sensitivity", ([400, 800], 1))]


def test_apply_polling_rate_wire_format(written):
    device.apply(MouseConfig(polling_rate=500), {"polling"})
    # 0x2B polling rate | 0x40 wireless flag; 500 Hz = 0x01. Then 0x11|0x40 = save.
    assert written == [packets("6b 01", "51 00")]


def test_apply_without_save(written):
    device.apply(MouseConfig(polling_rate=125), {"polling"}, save=False)
    assert written == [packets("6b 03")]


def test_apply_dpi_wire_format(written):
    device.apply(MouseConfig(dpi_presets=[400, 800, 1600], dpi_active=2), {"dpi"}, save=False)
    # 0x2D|0x40, count 3, selected index 1, then one byte per DPI
    assert written == [packets("6d 03 01 04 09 12")]


def test_apply_static_lighting_wire_format(written):
    config = MouseConfig(lighting="static", top_color="#112233", middle_color="red",
                         bottom_color="blue", reactive_color="#00ff00")
    device.apply(config, {"lighting"}, save=False)
    assert written == [packets(
        "61 01 00 11 22 33",  # zone 1 (top)
        "61 01 01 ff 00 00",  # zone 2 (middle)
        "61 01 02 00 00 ff",  # zone 3 (bottom)
        "66 01 00 00 ff 00",  # reactive color on
    )]


def test_apply_everything_sends_every_setting(written):
    device.apply(MouseConfig())
    sent = written[0]
    for command in ("6d", "6b", "69", "63", "67", "61 01 00", "66", "62 ff", "51 00"):
        assert packets(command) in sent


def test_battery_none_when_mouse_does_not_answer():
    # The simulated device answers with zeros, like a sleeping mouse
    assert device.read_battery() is None


def test_battery_retries_until_a_dozing_mouse_answers(monkeypatch):
    answers = iter([{"level": None, "is_charging": None}, {"level": 65, "is_charging": False}])
    monkeypatch.setattr(rivalcfg.mouse.Mouse, "battery", property(lambda self: next(answers)))
    assert device.read_battery() == device.Battery(level=65, charging=False)


def test_device_not_found(monkeypatch):
    monkeypatch.setattr(rivalcfg.devices, "list_plugged_devices", lambda: iter([]))
    with pytest.raises(device.DeviceNotFound):
        device.apply(MouseConfig())
    with pytest.raises(device.DeviceNotFound):
        device.describe()


def test_other_steelseries_mice_are_ignored(monkeypatch):
    rival = {"vendor_id": 0x1038, "product_id": 0x1702, "name": "Rival 100"}
    monkeypatch.setattr(rivalcfg.devices, "list_plugged_devices", lambda: iter([rival]))
    with pytest.raises(device.DeviceNotFound):
        device.describe()


def test_describe():
    assert device.describe() == "SteelSeries Aerox 9 Wireless (2.4 GHz)"


def test_uses_hidraw_backend():
    import rivalcfg.usbhid

    assert rivalcfg.usbhid.hid.__name__ == "hidraw"
