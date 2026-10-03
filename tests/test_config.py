import pytest

from aerox9ctl import config as cfg
from aerox9ctl.config import ConfigError, MouseConfig


def test_defaults_match_factory_settings():
    config = MouseConfig()
    assert config.dpi_presets == (400, 800, 1200, 2400, 3200)
    assert config.lighting == "rainbow"
    assert config.startup_lighting == "rainbow"


def test_toml_round_trip():
    config = MouseConfig(dpi_presets=[800, 1600], dpi_active=2, lighting="static", reactive_color="aqua")
    assert MouseConfig.from_toml(config.to_toml()) == config


def test_partial_toml_uses_defaults():
    config = MouseConfig.from_toml('polling_rate = 500\ntop_color = "red"\n')
    assert config.polling_rate == 500
    assert config.top_color == "#ff0000"
    assert config.dpi_presets == MouseConfig().dpi_presets


@pytest.mark.parametrize(
    "value, expected",
    [("red", "#ff0000"), ("#F00", "#ff0000"), ("00FF7f", "#00ff7f"), (" #123456 ", "#123456")],
)
def test_colors_are_normalized(value, expected):
    assert MouseConfig(top_color=value).top_color == expected


@pytest.mark.parametrize("value", ["off", "OFF", "none", "disable"])
def test_reactive_color_can_be_off(value):
    assert MouseConfig(reactive_color=value).reactive_color == "off"


@pytest.mark.parametrize(
    "color, brightness, expected",
    [("#ff8000", 100, "#ff8000"), ("#ff8000", 50, "#804000"), ("#ffffff", 0, "#000000"), ("off", 30, "off")],
)
def test_dim_color(color, brightness, expected):
    assert cfg.dim_color(color, brightness) == expected


def test_dpi_accepts_strings():
    assert MouseConfig(dpi_presets="400, 800,1600").dpi_presets == (400, 800, 1600)


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"dpi_presets": [150]}, "steps of 100"),
        ({"dpi_presets": [18100]}, "100-18000"),
        ({"dpi_presets": [400] * 6}, "1 to 5"),
        ({"dpi_presets": []}, "1 to 5"),
        ({"dpi_presets": "fast"}, "invalid DPI list"),
        ({"dpi_presets": [400, 800], "dpi_active": 3}, "dpi_active"),
        ({"dpi_active": 0}, "dpi_active"),
        ({"polling_rate": 300}, "polling_rate"),
        ({"lighting": "disco"}, "lighting"),
        ({"startup_lighting": "static"}, "startup_lighting"),
        ({"top_color": "notacolor"}, "invalid color"),
        ({"reactive_color": "#12"}, "invalid color"),
        ({"sleep_timer": 21}, "sleep_timer"),
        ({"dim_timer": -1}, "dim_timer"),
        ({"dim_timer": True}, "integer"),
        ({"sleep_timer": "5"}, "integer"),
        ({"brightness": 101}, "brightness"),
        ({"brightness": -1}, "brightness"),
    ],
)
def test_invalid_values_are_rejected(changes, message):
    with pytest.raises(ConfigError, match=message):
        MouseConfig(**changes)


def test_unknown_keys_are_rejected():
    with pytest.raises(ConfigError, match="unknown setting.*dpi_preset"):
        MouseConfig.from_toml("dpi_preset = [400]\n")


def test_broken_toml_is_a_config_error():
    with pytest.raises(ConfigError, match="invalid TOML"):
        MouseConfig.from_toml("dpi = [\n")


def test_state_round_trip():
    assert cfg.load_state() is None
    config = MouseConfig(polling_rate=500)
    cfg.save_state(config)
    assert cfg.load_state() == config
    assert cfg.state_path().parent.name == "aerox9ctl"


def test_profiles(tmp_path):
    assert cfg.list_profiles() == []
    cfg.save_file(MouseConfig(), cfg.profile_path("Gaming"))
    cfg.save_file(MouseConfig(), cfg.profile_path("work-2"))
    assert cfg.list_profiles() == ["Gaming", "work-2"]
    assert cfg.resolve_profile("Gaming") == cfg.profile_path("Gaming")
    assert cfg.resolve_profile("some/file.toml").name == "file.toml"
    assert cfg.resolve_profile(str(tmp_path / "x")) == tmp_path / "x"


@pytest.mark.parametrize("name", ["", "../escape", "a/b", ".hidden", "x" * 65])
def test_bad_profile_names(name):
    with pytest.raises(ConfigError, match="invalid profile name"):
        cfg.profile_path(name)


def test_load_file_reports_path(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text("polling_rate = 3\n")
    with pytest.raises(ConfigError, match="bad.toml: polling_rate"):
        cfg.load_file(path)
