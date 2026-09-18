"""Naplozas beallitasa: fajlba iras (a web UI a fajlt polling-gal olvassa)."""

from __future__ import annotations

import logging
import os

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "csengetes.log")


def setup_logging() -> None:
    logger = logging.getLogger("csengetes")
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")

    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
