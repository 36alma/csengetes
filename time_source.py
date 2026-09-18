"""Idoforras kezeles: NTP lekerdezes vagy teljes kezi felulbiralas."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

import ntplib

logger = logging.getLogger("csengetes")


class TimeSource:
    """Szolgaltatja az aktualis idot NTP vagy kezi mod szerint."""

    def __init__(self, ntp_server: str = "pool.ntp.org"):
        self._lock = threading.Lock()
        self._mode = "ntp"  # "ntp" vagy "manual"
        self._ntp_server = ntp_server

        # NTP mod allapota: az utolso sikeres szinkron idopontja es az akkor kapott ido
        self._ntp_synced_at_monotonic: float | None = None
        self._ntp_synced_value: datetime | None = None

        # Kezi mod allapota: a felhasznalo altal megadott ido es a beallitas pillanata
        self._manual_set_at_monotonic: float | None = None
        self._manual_value: datetime | None = None

    def set_ntp_server(self, host: str) -> None:
        with self._lock:
            self._ntp_server = host

    def get_ntp_server(self) -> str:
        with self._lock:
            return self._ntp_server

    def set_mode(self, mode: str) -> None:
        if mode not in ("ntp", "manual"):
            raise ValueError(f"Ismeretlen mod: {mode}")
        with self._lock:
            self._mode = mode
        logger.info("Idoforras mod valtas: %s", mode)

    def get_mode(self) -> str:
        with self._lock:
            return self._mode

    def sync_now(self) -> datetime:
        """Lekeri az idot az NTP szerverrol, elmenti referenciakent, es visszaadja."""
        import time as _time

        client = ntplib.NTPClient()
        with self._lock:
            server = self._ntp_server
        response = client.request(server, version=3, timeout=5)
        ntp_time = datetime.fromtimestamp(response.tx_time)
        with self._lock:
            self._ntp_synced_value = ntp_time
            self._ntp_synced_at_monotonic = _time.monotonic()
        logger.info("NTP szinkron sikeres (%s): %s", server, ntp_time.isoformat())
        return ntp_time

    def set_manual(self, value: datetime) -> None:
        import time as _time

        with self._lock:
            self._manual_value = value
            self._manual_set_at_monotonic = _time.monotonic()
            self._mode = "manual"
        logger.info("Kezi ido beallitva: %s", value.isoformat())

    def get_now(self) -> datetime:
        import time as _time

        with self._lock:
            mode = self._mode
            if mode == "manual":
                if self._manual_value is None:
                    return datetime.now()
                elapsed = _time.monotonic() - self._manual_set_at_monotonic
                return self._manual_value + timedelta(seconds=elapsed)
            else:
                if self._ntp_synced_value is None:
                    return datetime.now()
                elapsed = _time.monotonic() - self._ntp_synced_at_monotonic
                return self._ntp_synced_value + timedelta(seconds=elapsed)
