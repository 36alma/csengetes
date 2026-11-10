"""A mikrofon olvasása a mixer formátumában."""

from __future__ import annotations

from typing import Callable

from .protocol import BLOCK_SAMPLES, SAMPLE_RATE


class MicCapture:
    def __init__(self, on_block: Callable[[bytes], None], device: int | None = None) -> None:
        self._on_block = on_block
        self._device = device
        self._stream = None

    def start(self) -> None:
        import sounddevice as sd  # csak itt kell: a --set-token hangeszköz nélkül is fusson

        self._stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            blocksize=BLOCK_SAMPLES,
            channels=1,
            dtype="int16",
            device=self._device,
            callback=self._on_audio,
        )
        self._stream.start()

    def stop(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()

    def _on_audio(self, indata, frames, time_info, status) -> None:
        self._on_block(bytes(indata))


def list_inputs() -> list[tuple[int, str]]:
    import sounddevice as sd

    return [(i, d["name"]) for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]
