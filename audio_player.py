"""Hangfajl lejatszas kivalasztott kimeneti eszkozon (sounddevice + pydub)."""

from __future__ import annotations

import logging
import threading

import numpy as np
import sounddevice as sd
from pydub import AudioSegment

logger = logging.getLogger("csengetes")


class AudioPlayerError(Exception):
    pass


MAX_VOLUME_PERCENT = 200.0


class AudioPlayer:
    def __init__(self):
        self._device_index: int | None = None  # None = rendszer alapertelmezett
        self._volume_percent: float = 100.0  # 100 = eredeti hangero, 100 felett = boost

    def list_output_devices(self) -> list[tuple[int, str]]:
        devices = sd.query_devices()
        result = []
        for idx, dev in enumerate(devices):
            if dev.get("max_output_channels", 0) > 0:
                result.append((idx, dev["name"]))
        return result

    def set_device(self, index: int | None) -> None:
        self._device_index = index
        logger.info("Kimeneti eszkoz beallitva: %s", index)

    def get_device(self) -> int | None:
        return self._device_index

    def set_volume(self, percent: float) -> None:
        percent = max(0.0, min(MAX_VOLUME_PERCENT, percent))
        self._volume_percent = percent
        logger.info("Hangero beallitva: %.0f%%", percent)

    def get_volume(self) -> float:
        return self._volume_percent

    def play(
        self,
        filepath: str,
        blocking: bool = False,
        force: bool = False,
        on_done=None,
    ) -> None:
        """Lejatssza a megadott hangfajlt. Alapertelmezetten nem blokkolo (kulon szalon fut).

        force=True eseten a lejatszas eloszor megszakitja a folyamatban levo hangot.
        on_done(error: str | None) - ha meg van adva, a lejatszas vegen (siker vagy hiba
        eseten is) meghivodik a lejatszo szalon; a hivonak kell a fo szalra atterelnie
        (pl. GUI.after(0, ...)) ha az UI-t erinti.
        """

        def _do_play():
            error = None
            try:
                if force:
                    sd.stop()
                segment = AudioSegment.from_file(filepath)
                samples = np.array(segment.get_array_of_samples())
                if segment.channels > 1:
                    samples = samples.reshape((-1, segment.channels))
                samples = samples.astype(np.float32) / (2 ** (8 * segment.sample_width - 1))
                gain = self._volume_percent / 100.0
                if gain != 1.0:
                    samples = np.clip(samples * gain, -1.0, 1.0)
                sd.play(samples, samplerate=segment.frame_rate, device=self._device_index)
                sd.wait()
                logger.info("Lejatszas kesz: %s", filepath)
            except Exception as exc:
                logger.error("Hiba a lejatszas soran (%s): %s", filepath, exc)
                error = str(exc)
            finally:
                if on_done is not None:
                    on_done(error)
            if error is not None and on_done is None:
                raise AudioPlayerError(error)

        if blocking:
            _do_play()
        else:
            threading.Thread(target=_do_play, daemon=True).start()
