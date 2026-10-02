"""Mouse configuration model, validation and TOML profile storage.

A :class:`MouseConfig` is the complete set of settings the tool manages. It is
what gets written to profile files, to the "last applied" state file, and what
the CLI and GUI edit.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

from rivalcfg.color_helpers import is_color, parse_color_string

from . import buttons as btn

DPI_MIN, DPI_MAX, DPI_STEP = 100, 18000, 100
MAX_DPI_PRESETS = 5
POLLING_RATES = (125, 250, 500, 1000)
LIGHTING_EFFECTS = ("static", "rainbow")
# What the mouse shows at power-on, before any software re-applies colors.
# The Aerox 9 cannot store static colors in its onboard memory.
STARTUP_LIGHTING = ("off", "reactive", "rainbow", "reactive-rainbow")
SLEEP_TIMER_MAX = 20  # minutes
DIM_TIMER_MAX = 1200  # seconds

_PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")


class ConfigError(ValueError):
    """A setting has an invalid value, or a profile file is malformed."""


def normalize_color(value: str, *, allow_off: bool = False) -> str:
    """Return ``value`` as ``#rrggbb`` (or ``"off"`` when allowed)."""
    if not isinstance(value, str):
        raise ConfigError(f"color must be a string, got {value!r}")
    text = value.strip()
    if allow_off and text.lower() in ("off", "none", "disable", "disabled"):
        return "off"
    if not is_color(text):
        raise ConfigError(f"invalid color {value!r} (use #rrggbb, #rgb or a color name)")
    return "#%02x%02x%02x" % parse_color_string(text)


def parse_dpi_list(value) -> tuple[int, ...]:
    """Accept ``"400,800"``, ``[400, 800]`` or ``800`` and return a tuple of ints."""
    if isinstance(value, int) and not isinstance(value, bool):
        items = [value]
    elif isinstance(value, str):
        items = [part for part in value.replace(" ", "").split(",") if part]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise ConfigError(f"invalid DPI list {value!r}")
    try:
        return tuple(int(item) for item in items)
    except (TypeError, ValueError):
        raise ConfigError(f"invalid DPI list {value!r}") from None


def _check_int(name: str, value, low: int, high: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigError(f"{name} must be an integer, got {value!r}")
    if not low <= value <= high:
        raise ConfigError(f"{name} must be between {low} and {high}, got {value}")


@dataclass(frozen=True)
class MouseConfig:
    """All managed settings. Defaults match the mouse's factory settings."""

    dpi_presets: tuple[int, ...] = (400, 800, 1200, 2400, 3200)
    dpi_active: int = 1  # 1-based index into dpi_presets
    polling_rate: int = 1000
    lighting: str = "rainbow"
    top_color: str = "#ff0000"
    middle_color: str = "#00ff00"
    bottom_color: str = "#0000ff"
    reactive_color: str = "off"
    startup_lighting: str = "rainbow"
    sleep_timer: int = 5  # minutes, 0 = never
    dim_timer: int = 30  # seconds, 0 = never
    # Full button mapping as ((button, action), ...), or None to leave the
    # mouse's mapping alone (it cannot be read back, so we never guess).
    buttons: tuple[tuple[str, str], ...] | None = None

    def __post_init__(self):
        # Normalize in place (the dataclass is frozen, hence object.__setattr__)
        presets = parse_dpi_list(self.dpi_presets)
        if not 1 <= len(presets) <= MAX_DPI_PRESETS:
            raise ConfigError(f"dpi_presets needs 1 to {MAX_DPI_PRESETS} values, got {len(presets)}")
        for dpi in presets:
            if not DPI_MIN <= dpi <= DPI_MAX or dpi % DPI_STEP:
                raise ConfigError(
                    f"DPI {dpi} is invalid (must be {DPI_MIN}-{DPI_MAX} in steps of {DPI_STEP})"
                )
        object.__setattr__(self, "dpi_presets", presets)
        _check_int("dpi_active", self.dpi_active, 1, len(presets))

        if self.polling_rate not in POLLING_RATES:
            raise ConfigError(f"polling_rate must be one of {POLLING_RATES}, got {self.polling_rate!r}")
        if self.lighting not in LIGHTING_EFFECTS:
            raise ConfigError(f"lighting must be one of {LIGHTING_EFFECTS}, got {self.lighting!r}")
        if self.startup_lighting not in STARTUP_LIGHTING:
            raise ConfigError(
                f"startup_lighting must be one of {STARTUP_LIGHTING}, got {self.startup_lighting!r}"
            )
        for name in ("top_color", "middle_color", "bottom_color"):
            object.__setattr__(self, name, normalize_color(getattr(self, name)))
        object.__setattr__(self, "reactive_color", normalize_color(self.reactive_color, allow_off=True))
        _check_int("sleep_timer", self.sleep_timer, 0, SLEEP_TIMER_MAX)
        _check_int("dim_timer", self.dim_timer, 0, DIM_TIMER_MAX)
        if self.buttons is not None:
            if not isinstance(self.buttons, (dict, list, tuple)):
                raise ConfigError(f"buttons must be a table of button = action, got {self.buttons!r}")
            try:
                object.__setattr__(self, "buttons", btn.normalize_mapping(self.buttons))
            except ValueError as error:
                raise ConfigError(f"buttons: {error}") from None

    @classmethod
    def from_dict(cls, data: dict) -> MouseConfig:
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ConfigError(f"unknown setting(s): {', '.join(unknown)}")
        return cls(**data)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["dpi_presets"] = list(self.dpi_presets)
        if self.buttons is None:
            del data["buttons"]
        else:
            data["buttons"] = dict(self.buttons)
        return data

    def merged(self, **changes) -> MouseConfig:
        """Return a copy with ``changes`` applied (and validated)."""
        return replace(self, **changes)

    def to_toml(self) -> str:
        lines = ["# aerox9ctl profile for the SteelSeries Aerox 9 Wireless"]
        data = self.to_dict()
        buttons = data.pop("buttons", None)
        for key, value in data.items():
            if isinstance(value, list):
                rendered = "[" + ", ".join(str(v) for v in value) + "]"
            elif isinstance(value, str):
                rendered = json.dumps(value)  # JSON strings are valid TOML basic strings
            else:
                rendered = str(value)
            lines.append(f"{key} = {rendered}")
        if buttons is not None:
            lines += ["", "[buttons]"]
            lines += [f"{name} = {json.dumps(action)}" for name, action in buttons.items()]
        return "\n".join(lines) + "\n"

    @classmethod
    def from_toml(cls, text: str) -> MouseConfig:
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as error:
            raise ConfigError(f"invalid TOML: {error}") from None
        return cls.from_dict(data)


# --- Storage ---------------------------------------------------------------


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "aerox9ctl"


def state_path() -> Path:
    """File holding the configuration that was last applied to the mouse."""
    return config_dir() / "current.toml"


def profiles_dir() -> Path:
    return config_dir() / "profiles"


def load_file(path: Path) -> MouseConfig:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"no such file: {path}") from None
    try:
        return MouseConfig.from_toml(text)
    except ConfigError as error:
        raise ConfigError(f"{path}: {error}") from None


def save_file(config: MouseConfig, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(config.to_toml(), encoding="utf-8")
    tmp.replace(path)


def load_state() -> MouseConfig | None:
    """The last applied configuration, or ``None`` if nothing was applied yet."""
    if not state_path().is_file():
        return None
    return load_file(state_path())


def save_state(config: MouseConfig) -> None:
    save_file(config, state_path())


def check_profile_name(name: str) -> str:
    if not _PROFILE_NAME_RE.match(name):
        raise ConfigError(
            f"invalid profile name {name!r} (letters, digits, space, '_', '-', '.'; max 64 chars)"
        )
    return name


def profile_path(name: str) -> Path:
    return profiles_dir() / f"{check_profile_name(name)}.toml"


def list_profiles() -> list[str]:
    if not profiles_dir().is_dir():
        return []
    return sorted(p.stem for p in profiles_dir().glob("*.toml"))


def resolve_profile(name_or_path: str) -> Path:
    """A saved profile name, or a path to a TOML file."""
    candidate = Path(name_or_path).expanduser()
    if candidate.suffix == ".toml" or os.sep in name_or_path:
        return candidate
    return profile_path(name_or_path)
