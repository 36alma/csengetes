"""Eszkozesemenyek naploja: `[device:<nev>] esemeny kulcs=ertek`."""

from __future__ import annotations

import logging

logger = logging.getLogger("csengetes")


def log_event(name: str | None, event: str, level: int = logging.INFO, **fields) -> None:
    """Egy eszkozesemeny a kozos naplofajlba. A tokent/hash-t a hivo sosem adja at."""
    extra = "".join(f" {key}={value}" for key, value in fields.items())
    logger.log(level, "[device:%s] %s%s", name if name else "?", event, extra)
