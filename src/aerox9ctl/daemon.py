"""Keep the live lighting applied.

The Aerox 9 forgets static colors whenever it powers on, wakes up or
reconnects, falling back to its startup lighting. This loop polls the mouse
and re-applies the lighting from the last applied configuration each time the
mouse comes (back) online.
"""

from __future__ import annotations

import logging
import time

from . import config as cfg
from . import device

log = logging.getLogger(__name__)


def _is_online() -> bool:
    try:
        return device.read_battery() is not None
    except device.DeviceNotFound:
        return False


def run(
    interval: float = 5.0,
    *,
    is_online=_is_online,
    load_state=cfg.load_state,
    apply=device.apply,
    sleep=time.sleep,
    max_iterations: int | None = None,
) -> None:
    """Poll forever (or ``max_iterations`` times, for tests)."""
    online = False
    iteration = 0
    while max_iterations is None or iteration < max_iterations:
        iteration += 1
        try:
            now_online = is_online()
        except OSError as error:
            log.warning("cannot reach the mouse: %s", error)
            now_online = False

        if now_online and not online:
            try:
                state = load_state()
                if state is None:
                    log.info("mouse online; nothing applied yet, leaving lighting alone")
                else:
                    apply(state, {device.GROUP_LIGHTING}, save=False)
                    log.info("mouse online; lighting re-applied")
            except (OSError, device.DeviceNotFound, cfg.ConfigError) as error:
                log.warning("re-applying lighting failed, will retry: %s", error)
                now_online = False
        elif online and not now_online:
            log.info("mouse went offline (asleep, off or unplugged)")

        online = now_online
        sleep(interval)
