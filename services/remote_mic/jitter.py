"""Hálózatról érkező hangblokkok puffere a keverőszál elé.

A küldő és a mixer órája nem jár együtt, a hálózat pedig szakaszosan szállít. A puffer
a hálózat viselkedéséhez igazítja a célszintjét, a késleltetést pedig kordában tartja.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable

import numpy as np

START_TARGET = 3  # kb. 78 ms
MAX_TARGET = 8  # kb. 209 ms
HARD_LIMIT = 16
EXCESS_BLOCKS = 2  # ennyivel a cél fölött számít tartós többletnek
RECOVER_SECONDS = 60.0
DRIFT_HOLD_SECONDS = 5.0


class JitterBuffer:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._blocks: deque[np.ndarray] = deque()
        self._reset()

    def _reset(self) -> None:
        self._blocks.clear()
        self._target = START_TARGET
        self._priming = True
        self._clean_since = self._clock()
        self._excess_since: float | None = None

    def clear(self) -> None:
        with self._lock:
            self._reset()

    def feed(self, block: np.ndarray) -> None:
        with self._lock:
            now = self._clock()
            self._blocks.append(block)
            while len(self._blocks) > HARD_LIMIT:
                self._blocks.popleft()
            if self._priming and len(self._blocks) >= self._target:
                self._priming = False
                self._clean_since = now
            self._trim_drift(now)

    def _trim_drift(self, now: float) -> None:
        """Tartós többletnél egyetlen blokkot dob el, hogy a kiesés rövid maradjon."""
        if len(self._blocks) < self._target + EXCESS_BLOCKS:
            self._excess_since = None
        elif self._excess_since is None:
            self._excess_since = now
        elif now - self._excess_since >= DRIFT_HOLD_SECONDS:
            self._blocks.popleft()
            self._excess_since = now

    def read_block(self) -> np.ndarray | None:
        """A következő blokk, vagy None (csend). Nem blokkol: a keverőszál hívja."""
        with self._lock:
            if self._priming:
                return None
            now = self._clock()
            if not self._blocks:
                self._target = min(self._target + 1, MAX_TARGET)
                self._priming = True
                self._clean_since = now
                self._excess_since = None
                return None
            if self._target > START_TARGET and now - self._clean_since >= RECOVER_SECONDS:
                self._target -= 1
                self._clean_since = now
            return self._blocks.popleft()
