"""Command line interface."""

from __future__ import annotations

import argparse
import logging
import sys

from . import __version__
from . import buttons as btn
from . import config as cfg
from . import device

_PERMISSION_HINT = (
    "If this is a permission problem, install the udev rule from "
    "packaging/70-aerox9ctl.rules on the host (see README) and re-plug the dongle."
)


def _battery_text(battery: device.Battery | None) -> str:
    if battery is None:
        return "unavailable (mouse asleep or off?)"
    return f"{battery.level}% ({'charging' if battery.charging else 'discharging'})"


def cmd_status(args) -> int:
    print(f"Device:  {device.describe()}")
    print(f"Battery: {_battery_text(device.read_battery())}")
    state = cfg.load_state()
    if state is None:
        print("\nNo configuration applied by aerox9ctl yet (the mouse's settings are unknown).")
    else:
        print(f"\nLast applied configuration ({cfg.state_path()}):\n")
        print(state.to_toml(), end="")
    return 0


def cmd_battery(args) -> int:
    battery = device.read_battery()
    print(_battery_text(battery))
    return 0 if battery else 1


def _changes_from_args(args) -> dict:
    changes = {}
    if args.dpi is not None:
        changes["dpi_presets"] = cfg.parse_dpi_list(args.dpi)
    if args.dpi_active is not None:
        changes["dpi_active"] = args.dpi_active
    if args.polling_rate is not None:
        changes["polling_rate"] = args.polling_rate
    if args.color is not None:
        for zone in ("top_color", "middle_color", "bottom_color"):
            changes[zone] = args.color
    for zone in ("top_color", "middle_color", "bottom_color"):
        if getattr(args, zone) is not None:
            changes[zone] = getattr(args, zone)
    if any(zone in changes for zone in ("top_color", "middle_color", "bottom_color")):
        changes["lighting"] = "static"  # picking a color means you want to see it
    if args.lighting is not None:
        changes["lighting"] = args.lighting
    for name in ("reactive_color", "brightness", "startup_lighting", "sleep_timer", "dim_timer"):
        if getattr(args, name) is not None:
            changes[name] = getattr(args, name)
    return changes


def cmd_set(args) -> int:
    changes = _changes_from_args(args)
    if not changes:
        print("Nothing to set. See: aerox9ctl set --help", file=sys.stderr)
        return 2
    old = cfg.load_state()
    if old is None:
        print("note: no previous state; unspecified settings are assumed to be factory defaults")
    base = old or cfg.MouseConfig()
    if args.dpi is not None and args.dpi_active is None:
        # Keep the active preset if it still exists in the new list
        changes["dpi_active"] = min(base.dpi_active, len(changes["dpi_presets"]))
    new = base.merged(**changes)
    groups = device.changed_groups(base, new) if old else frozenset(
        device.FIELD_GROUPS[name] for name in changes
    )
    device.apply(new, groups, save=not args.no_save)
    cfg.save_state(new)
    print("Applied." + ("" if args.no_save else " Saved to the mouse."))
    return 0


def cmd_apply(args) -> int:
    path = cfg.resolve_profile(args.profile)
    new = cfg.load_file(path)
    device.apply(new, save=not args.no_save)
    cfg.save_state(new)
    print(f"Applied {path}." + ("" if args.no_save else " Saved to the mouse."))
    return 0


def cmd_reset(args) -> int:
    old = cfg.load_state()
    # The button mapping has its own reset ('buttons reset'): keep it as it is.
    new = cfg.MouseConfig(buttons=old.buttons if old else None)
    device.apply(new, save=True)
    cfg.save_state(new)
    print("Factory default settings applied and saved to the mouse (button mapping unchanged).")
    return 0


def cmd_profile(args) -> int:
    if args.action == "list":
        names = cfg.list_profiles()
        print("\n".join(names) if names else f"No saved profiles in {cfg.profiles_dir()}")
        return 0
    if args.action == "show":
        if args.name:
            print(cfg.load_file(cfg.resolve_profile(args.name)).to_toml(), end="")
        else:
            state = cfg.load_state()
            print((state or cfg.MouseConfig()).to_toml(), end="")
        return 0
    if not args.name:
        raise cfg.ConfigError(f"'profile {args.action}' needs a profile name")
    path = cfg.profile_path(args.name)
    if args.action == "save":
        state = cfg.load_state()
        if state is None:
            raise cfg.ConfigError("nothing applied yet: apply or set a configuration first")
        cfg.save_file(state, path)
        print(f"Saved current configuration as {path}")
    elif args.action == "delete":
        if not path.is_file():
            raise cfg.ConfigError(f"no profile named {args.name!r}")
        path.unlink()
        print(f"Deleted {path}")
    return 0


def _print_mapping(mapping: dict[str, str]) -> None:
    width = max(len(btn.LABELS[name]) for name in btn.BUTTON_NAMES)
    for name in btn.BUTTON_NAMES:
        marker = "" if mapping[name] == btn.DEFAULTS[name] else "   (changed)"
        print(f"  {btn.LABELS[name]:<{width}}  {name:<11} -> {mapping[name]}{marker}")


def cmd_buttons(args) -> int:
    if args.action == "actions":
        print("Mouse buttons: " + ", ".join(btn.MOUSE_ACTIONS))
        print("Special:       " + ", ".join(btn.SPECIAL_ACTIONS))
        print("Media keys:    " + ", ".join(btn.MEDIA_KEYS))
        print("Keyboard keys: " + ", ".join(btn.KEYBOARD_KEYS))
        print("\nCombine up to 4 keyboard keys with '+', e.g. LeftShift+1, ctrl+c, alt+tab.")
        print("Key names are US-layout key positions: on a German layout '-' is ß, '=' is ´,")
        print("'[' is Ü, ';' is Ö, \"'\" is Ä, 'Y' is Z and 'Z' is Y. Macros are not supported.")
        return 0
    if args.action == "test":
        from . import tester

        print("Press buttons on the mouse (Ctrl+C to stop). Nothing is sent to the mouse.")
        print("The DPI button and disabled buttons produce no output.\n")
        try:
            tester.listen(lambda event: print(event, flush=True))
        except KeyboardInterrupt:
            pass
        return 0

    state = cfg.load_state()
    if args.action == "show":
        if state is None or state.buttons is None:
            print("Button mapping not managed by aerox9ctl yet: the mouse keeps whatever it has")
            print("(the mapping cannot be read back). Use 'aerox9ctl buttons test' to see what")
            print("each button sends. Factory default mapping:\n")
            _print_mapping(btn.DEFAULTS)
        else:
            _print_mapping(dict(state.buttons))
        return 0

    if args.action == "reset":
        mapping = dict(btn.DEFAULTS)
    else:  # set
        if not args.assignments:
            raise cfg.ConfigError("give at least one BUTTON=ACTION, e.g. side1=F13")
        mapping = dict(state.buttons) if state and state.buttons else dict(btn.DEFAULTS)
        for assignment in args.assignments:
            name, sep, action = assignment.partition("=")
            if not sep or not name or not action:
                raise cfg.ConfigError(f"expected BUTTON=ACTION, got {assignment!r}")
            mapping[name.strip().lower()] = action.strip()
    base = state or cfg.MouseConfig()
    new = base.merged(buttons=mapping)
    device.apply(new, {device.GROUP_BUTTONS}, save=not args.no_save)
    cfg.save_state(new)
    print("Button mapping applied." + ("" if args.no_save else " Saved to the mouse."))
    _print_mapping(dict(new.buttons))
    return 0


def cmd_daemon(args) -> int:
    from . import daemon

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        daemon.run(interval=args.interval)
    except KeyboardInterrupt:
        pass
    return 0


def cmd_gui(args) -> int:
    try:
        from . import gui
    except ImportError as error:
        print(f"error: the GUI needs PySide6 ({error}). Install with: pip install 'aerox9ctl[gui]'", file=sys.stderr)
        return 1
    return gui.main()


def cmd_tray(args) -> int:
    try:
        from . import tray
    except ImportError as error:
        print(f"error: the tray icon needs PySide6 ({error}). Install with: pip install 'aerox9ctl[gui]'", file=sys.stderr)
        return 1
    return tray.main()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aerox9ctl",
        description="Configure the SteelSeries Aerox 9 Wireless.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    sub.add_parser("status", help="show device, battery and last applied settings").set_defaults(func=cmd_status)
    sub.add_parser("battery", help="show battery level").set_defaults(func=cmd_battery)

    p = sub.add_parser(
        "set",
        help="change individual settings",
        description="Change individual settings. Settings not given keep their last applied value.",
    )
    p.add_argument("--dpi", metavar="LIST", help=f"1-{cfg.MAX_DPI_PRESETS} DPI presets, e.g. 400,800,1600 "
                   f"({cfg.DPI_MIN}-{cfg.DPI_MAX}, steps of {cfg.DPI_STEP})")
    p.add_argument("--dpi-active", type=int, metavar="N", help="active DPI preset (1-based)")
    p.add_argument("--polling-rate", type=int, choices=cfg.POLLING_RATES, metavar="HZ",
                   help="polling rate: 125, 250, 500 or 1000")
    p.add_argument("--lighting", choices=cfg.LIGHTING_EFFECTS, help="lighting effect")
    p.add_argument("--color", metavar="COLOR", help="color for all three zones (implies --lighting static)")
    p.add_argument("--top-color", dest="top_color", metavar="COLOR")
    p.add_argument("--middle-color", dest="middle_color", metavar="COLOR")
    p.add_argument("--bottom-color", dest="bottom_color", metavar="COLOR")
    p.add_argument("--reactive-color", metavar="COLOR|off", help="flash color on click, or 'off'")
    p.add_argument("--brightness", type=int, metavar="PCT",
                   help=f"LED brightness for colors, 0-{cfg.BRIGHTNESS_MAX}%% (no effect on rainbow)")
    p.add_argument("--startup-lighting", choices=cfg.STARTUP_LIGHTING,
                   help="lighting the mouse shows at power-on (colors are not stored in the mouse)")
    p.add_argument("--sleep-timer", type=int, metavar="MIN", help=f"minutes until sleep, 0-{cfg.SLEEP_TIMER_MAX} (0 = never)")
    p.add_argument("--dim-timer", type=int, metavar="SEC", help=f"seconds until LEDs dim, 0-{cfg.DIM_TIMER_MAX} (0 = never)")
    p.add_argument("--no-save", action="store_true", help="don't write to the mouse's onboard memory")
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("apply", help="apply a saved profile or a TOML file")
    p.add_argument("profile", help="profile name or path to a .toml file")
    p.add_argument("--no-save", action="store_true", help="don't write to the mouse's onboard memory")
    p.set_defaults(func=cmd_apply)

    sub.add_parser("reset", help="apply factory default settings (except the button mapping)").set_defaults(func=cmd_reset)

    p = sub.add_parser("profile", help="manage saved profiles")
    p.add_argument("action", choices=("list", "show", "save", "delete"))
    p.add_argument("name", nargs="?", help="profile name ('show' without a name prints the current configuration)")
    p.set_defaults(func=cmd_profile)

    p = sub.add_parser(
        "buttons",
        help="show, test or change the button mapping",
        description="Button mapping. The mouse can't report its mapping, so 'show' prints what "
        "aerox9ctl last applied and 'test' shows what each button sends.",
        epilog="examples:\n  aerox9ctl buttons test\n  aerox9ctl buttons set side1=F13 side2=volup side3=shift+3 button4=disabled\n"
        "  aerox9ctl buttons set side12=default\n  aerox9ctl buttons reset",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("action", choices=("show", "test", "set", "reset", "actions"))
    p.add_argument("assignments", nargs="*", metavar="BUTTON=ACTION",
                   help=f"buttons: {', '.join(btn.BUTTON_NAMES)}; action 'default' restores one button")
    p.add_argument("--no-save", action="store_true", help="don't write to the mouse's onboard memory")
    p.set_defaults(func=cmd_buttons)

    p = sub.add_parser("daemon", help="re-apply lighting whenever the mouse wakes up or reconnects")
    p.add_argument("--interval", type=float, default=5.0, metavar="SEC", help="polling interval (default: 5)")
    p.set_defaults(func=cmd_daemon)

    sub.add_parser("gui", help="open the graphical interface").set_defaults(func=cmd_gui)
    sub.add_parser("tray", help="show a system tray icon with the battery level").set_defaults(func=cmd_tray)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except cfg.ConfigError as error:
        print(f"error: {error}", file=sys.stderr)
    except device.DeviceNotFound as error:
        print(f"error: {error}", file=sys.stderr)
    except OSError as error:
        print(f"error: cannot talk to the mouse: {error}\n{_PERMISSION_HINT}", file=sys.stderr)
    return 1
