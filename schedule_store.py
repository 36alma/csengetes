"""Csengetesi rendek (JSON) betoltese es mentese."""

from __future__ import annotations

import json
import logging
import os
import re

logger = logging.getLogger("csengetes")

SCHEDULES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schedules")

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class ScheduleError(Exception):
    pass


def _ensure_dir() -> None:
    os.makedirs(SCHEDULES_DIR, exist_ok=True)


def list_schedules() -> list[str]:
    _ensure_dir()
    return sorted(f for f in os.listdir(SCHEDULES_DIR) if f.endswith(".json"))


def _resolve_path(filename: str) -> str:
    if os.path.isabs(filename):
        raise ScheduleError(f"Ervenytelen fajlnev: {filename!r}")
    base = os.path.realpath(SCHEDULES_DIR)
    path = os.path.realpath(os.path.join(base, filename))
    if path != base and not path.startswith(base + os.sep):
        raise ScheduleError(f"Ervenytelen fajlnev: {filename!r}")
    return path


def load(filename: str) -> dict:
    path = _resolve_path(filename)
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    validate(data)
    return data


def save(filename: str, data: dict) -> None:
    validate(data)
    _ensure_dir()
    path = _resolve_path(filename)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    logger.info("Csengetesi rend mentve: %s", filename)


def validate(data: dict) -> None:
    if not isinstance(data, dict):
        raise ScheduleError("A rend gyoker eleme objektum kell legyen.")
    if "name" not in data or not isinstance(data["name"], str):
        raise ScheduleError("Hianyzik vagy ervenytelen a 'name' mezo.")
    if "events" not in data or not isinstance(data["events"], list):
        raise ScheduleError("Hianyzik vagy ervenytelen az 'events' mezo.")
    for ev in data["events"]:
        if not isinstance(ev, dict) or "time" not in ev or "file" not in ev:
            raise ScheduleError("Minden esemenynek 'time' es 'file' mezovel kell rendelkeznie.")
        if not _TIME_RE.match(ev["time"]):
            raise ScheduleError(f"Ervenytelen idopont formatum (HH:MM varva): {ev['time']!r}")
