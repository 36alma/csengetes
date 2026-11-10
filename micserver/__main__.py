"""A mikrofongép programja: python -m micserver."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import logging
import os
import sys
import time

from .capture import MicCapture, list_inputs
from .protocol import DEFAULT_PORT, is_valid_credential
from .server import MicServer, MicState

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULTS = {"name": "Mikrofongép", "host": "0.0.0.0", "port": DEFAULT_PORT, "input_device": None}


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def load_config(path: str) -> dict:
    """A config.json a beépített alapértékekre; hibás tartalomnál ValueError."""
    config = dict(DEFAULTS)
    if not os.path.isfile(path):
        return config
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)  # a hibás JSON is ValueError
    if not isinstance(raw, dict):
        raise ValueError("A konfiguráció nem objektum.")
    config.update({key: raw[key] for key in DEFAULTS if key in raw})
    if not isinstance(config["name"], str) or not 1 <= len(config["name"]) <= 64:
        raise ValueError("Érvénytelen 'name' (1-64 karakter).")
    if not isinstance(config["host"], str) or not config["host"]:
        raise ValueError("Érvénytelen 'host'.")
    if not _is_int(config["port"]) or not 1 <= config["port"] <= 65535:
        raise ValueError("Érvénytelen 'port' (1-65535).")
    if config["input_device"] is not None and not _is_int(config["input_device"]):
        raise ValueError("Érvénytelen 'input_device' (szám vagy null).")
    return config


def announce(text: str) -> None:
    """A gépnél ülők így látják, hogy a mikrofon élő."""
    print(f"{time.strftime('%H:%M:%S')}  *** {text} ***", flush=True)


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    logging.getLogger("websockets").setLevel(logging.WARNING)


def _set_token(state_path: str) -> int:
    value = getpass.getpass("Illeszd be a mixerben kapott tokent: ").strip()
    if not is_valid_credential(value):
        print("Ez nem érvényes token, a régi maradt.")
        return 2
    MicState(state_path).save(value, None)
    print("A token elmentve.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m micserver", description="Hálózati mikrofon a csengetési rendszer mixeréhez.")
    parser.add_argument("--config", default=os.path.join(PACKAGE_DIR, "config.json"))
    parser.add_argument("--state", default=os.path.join(PACKAGE_DIR, "state.json"))
    parser.add_argument("--set-token", action="store_true", help="a mixerben kapott token beállítása")
    parser.add_argument("--list-inputs", action="store_true", help="a bemeneti hangeszközök listája")
    args = parser.parse_args(argv)

    if args.list_inputs:
        for index, name in list_inputs():
            print(f"{index}: {name}")
        return 0
    if args.set_token:
        return _set_token(args.state)
    try:
        config = load_config(args.config)
    except ValueError as exc:
        print(f"Hibás konfiguráció: {exc}")
        return 2
    if MicState(args.state).load()["current"] is None:
        print("Még nincs token. Vedd fel a gépet a mixerben, majd futtasd: python -m micserver --set-token")
        return 2

    _configure_logging()
    server = MicServer(
        args.state,
        lambda on_block: MicCapture(on_block, config["input_device"]),
        name=config["name"],
        host=config["host"],
        port=config["port"],
        announce=announce,
    )
    announce("KI")
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        pass
    except OSError as exc:
        print(f"A szerver nem indítható (foglalt a {config['port']}-s port?): {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
