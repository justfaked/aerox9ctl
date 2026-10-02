import pytest
import rivalcfg.devices

from aerox9ctl import config as cfg
from aerox9ctl.cli import main
from conftest import packets


def test_set_without_state_sends_only_given_settings(written, capsys):
    assert main(["set", "--polling-rate", "500"]) == 0
    assert written == [packets("6b 01", "51 00")]
    assert cfg.load_state() == cfg.MouseConfig(polling_rate=500)
    assert "factory defaults" in capsys.readouterr().out


def test_set_with_state_sends_only_changed_groups(written):
    cfg.save_state(cfg.MouseConfig(lighting="static"))
    assert main(["set", "--color", "blue", "--no-save"]) == 0
    sent = written[0]
    assert packets("61 01 00 00 00 ff") in sent
    assert packets("6b") not in sent  # polling rate untouched
    assert packets("51 00") not in sent  # not saved
    state = cfg.load_state()
    assert (state.top_color, state.middle_color, state.bottom_color) == ("#0000ff",) * 3


def test_color_implies_static_lighting():
    cfg.save_state(cfg.MouseConfig(lighting="rainbow"))
    main(["set", "--top-color", "red"])
    assert cfg.load_state().lighting == "static"
    main(["set", "--top-color", "red", "--lighting", "rainbow"])
    assert cfg.load_state().lighting == "rainbow"


def test_set_dpi_keeps_active_preset_when_possible():
    cfg.save_state(cfg.MouseConfig(dpi_active=4))
    main(["set", "--dpi", "800,1600,3200,6400"])
    assert cfg.load_state().dpi_active == 4
    main(["set", "--dpi", "800,1600"])
    assert cfg.load_state().dpi_active == 2
    main(["set", "--dpi", "800,1600", "--dpi-active", "1"])
    assert cfg.load_state().dpi_active == 1


def test_set_nothing_is_an_error(capsys):
    assert main(["set"]) == 2


def test_invalid_value_is_reported_without_touching_the_mouse(written, capsys):
    assert main(["set", "--color", "notacolor"]) == 1
    assert "invalid color" in capsys.readouterr().err
    assert written == []
    assert cfg.load_state() is None


def test_apply_profile_by_name_and_path(written, tmp_path):
    cfg.save_file(cfg.MouseConfig(polling_rate=250), cfg.profile_path("work"))
    assert main(["apply", "work"]) == 0
    assert cfg.load_state().polling_rate == 250
    path = tmp_path / "custom.toml"
    path.write_text("polling_rate = 125\n")
    assert main(["apply", str(path)]) == 0
    assert cfg.load_state().polling_rate == 125
    assert len(written) == 2


def test_apply_missing_profile(capsys):
    assert main(["apply", "nope"]) == 1
    assert "no such file" in capsys.readouterr().err


def test_profile_commands(capsys):
    assert main(["profile", "save", "x"]) == 1  # nothing applied yet
    main(["set", "--polling-rate", "250"])
    assert main(["profile", "save", "gaming"]) == 0
    capsys.readouterr()
    main(["profile", "list"])
    assert capsys.readouterr().out.strip() == "gaming"
    main(["profile", "show", "gaming"])
    assert "polling_rate = 250" in capsys.readouterr().out
    assert main(["profile", "delete", "gaming"]) == 0
    assert cfg.list_profiles() == []
    assert main(["profile", "delete", "gaming"]) == 1


def test_reset(written):
    main(["set", "--polling-rate", "125"])
    assert main(["reset"]) == 0
    assert cfg.load_state() == cfg.MouseConfig()


def test_no_mouse(monkeypatch, capsys):
    monkeypatch.setattr(rivalcfg.devices, "list_plugged_devices", lambda: iter([]))
    assert main(["battery"]) == 1
    assert "no SteelSeries Aerox 9" in capsys.readouterr().err


def test_os_error_hints_at_udev(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise OSError("open failed")

    monkeypatch.setattr("rivalcfg.mouse.get_mouse", fail)
    assert main(["set", "--polling-rate", "500"]) == 1
    err = capsys.readouterr().err
    assert "open failed" in err and "udev" in err


def test_status(capsys):
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "Aerox 9 Wireless (2.4 GHz)" in out
    assert "No configuration applied" in out


@pytest.mark.parametrize("argv", [["set", "--polling-rate", "300"], ["set", "--startup-lighting", "x"]])
def test_argparse_rejects_bad_choices(argv):
    with pytest.raises(SystemExit):
        main(argv)


def test_reset_keeps_button_mapping(written):
    main(["buttons", "set", "side1=shift+1"])
    main(["set", "--polling-rate", "125"])
    assert main(["reset"]) == 0
    state = cfg.load_state()
    assert state.polling_rate == 1000
    assert dict(state.buttons)["side1"] == "LeftShift+1"
