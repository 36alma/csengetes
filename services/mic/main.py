import queue

import numpy as np
import sounddevice as sd
from model import InputDevice


class Mic():
    def __init__(self):
        self.current_output_device = InputDevice(index=sd.query_devices(kind="input")['index'],name=sd.query_devices(kind="input")['name'])
        self._volume = 1.0
        self._stream: sd.InputStream | None = None
        self._blocks: queue.Queue[np.ndarray] = queue.Queue(maxsize=8)

    @property
    def volume(self) -> float:
        return self._volume

    @volume.setter
    def volume(self, volume: float):
        if volume < 0.0 or volume > 1.0:
            raise ValueError("Volume must be between 0.0 and 1.0")
        self._volume = volume

    def listinput(self)->list[InputDevice]:
        seen = set()
        inputs:list[InputDevice] = []
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0 and d["name"] not in seen:
                seen.add(d["name"])
                inputs.append(InputDevice(index=i,name=d["name"]))
        return inputs

    def setoutputdevices(self,new_device:InputDevice):
        self.current_output_device = new_device

    def start(self, samplerate: int, blocksize: int):
        """Mono mikrofonfelvetel inditasa; a blokkok a read_block()-kal olvashatok."""
        self.stop()
        self._stream = sd.InputStream(
            samplerate=samplerate,
            blocksize=blocksize,
            channels=1,
            dtype="float32",
            device=self.current_output_device.index,
            callback=self._on_audio,
        )
        self._stream.start()

    def stop(self):
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        while not self._blocks.empty():
            self._blocks.get_nowait()

    def _on_audio(self, indata, frames, time_info, status):
        try:
            self._blocks.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass  # lemarado fogyaszto: a mikrofon nem blokkolhat

    def read_block(self) -> np.ndarray | None:
        try:
            return self._blocks.get_nowait()
        except queue.Empty:
            return None
