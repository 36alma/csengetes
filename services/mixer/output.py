"""Helyi hangkimenet (sounddevice) es a kimeneti eszkozok listaja."""

from __future__ import annotations

import sys

import numpy as np
import sounddevice as sd

from .mixing import BLOCK_SIZE, SAMPLE_RATE


class OutputSink:
    """Blokkolo irasu kimeneti folyam: a hardver orajele adja a mixer tempojat."""

    def __init__(self, stream):
        self._stream = stream

    def write(self, pcm: np.ndarray) -> None:
        self._stream.write(pcm.reshape(-1, 1))

    def close(self) -> None:
        self._stream.stop()
        self._stream.close()


def open_output_stream(device: int | None) -> OutputSink:
    extra = None
    if sys.platform == "win32":
        info = sd.query_devices(device, "output")
        if sd.query_hostapis(info["hostapi"])["name"] == "Windows WASAPI":
            extra = sd.WasapiSettings(auto_convert=True)  # az eszkoz sajat mintavetelere alakit
    stream = sd.OutputStream(
        samplerate=SAMPLE_RATE,
        blocksize=BLOCK_SIZE,
        channels=1,
        dtype="int16",
        device=device,
        extra_settings=extra,
    )
    stream.start()
    return OutputSink(stream)


def list_output_devices() -> list[tuple[int, str]]:
    """[(eszkozindex, nev)] az alapertelmezett host API-n, nev szerint egyedi."""
    host_api = sd.default.hostapi
    seen: set[str] = set()
    result: list[tuple[int, str]] = []
    for index, dev in enumerate(sd.query_devices()):
        if dev["max_output_channels"] > 0 and dev["hostapi"] == host_api and dev["name"] not in seen:
            seen.add(dev["name"])
            result.append((index, dev["name"]))
    return result
