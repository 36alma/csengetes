import threading
import time

import lameenc
import numpy as np
import soundfile as sf

from model import InputDevice, MixerStatus
from services.mic import Mic
from .buffer import PacketBuffer
from .mixing import BLOCK_SIZE, SAMPLE_RATE, mix_blocks, split_mp3_frames

BITRATE_KBPS = 128
LEVEL_DECAY = 0.85  # blokkonkenti lecsengés (~26 ms): a jelszint nem ugral, de gyorsan elhal


def load_music(filepath: str) -> np.ndarray:
    """Zene betoltese mono, SAMPLE_RATE-es float32 tombbe."""
    data, rate = sf.read(filepath, dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    if rate != SAMPLE_RATE:
        target = int(len(mono) * SAMPLE_RATE / rate)
        mono = np.interp(
            np.linspace(0, len(mono) - 1, target), np.arange(len(mono)), mono
        ).astype(np.float32)
    return mono


class Mixer():
    def __init__(self, mic: Mic | None = None, buffer: PacketBuffer | None = None):
        self.mic = mic if mic is not None else Mic()
        self.buffer = buffer if buffer is not None else PacketBuffer()
        self.master_volume = 1.0
        self.music_volume = 1.0
        self.status = MixerStatus.IDLE
        self.level = 0.0  # kimeneti csucsertek 0..1, blokkonkent lecsengetve (a feluleti meronek)
        self._music: np.ndarray | None = None
        self._music_pos = 0
        self._music_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def current_output_device(self):
        return self.mic.current_output_device

    @property
    def mic_volume(self) -> float:
        return self.mic.volume

    def change_output_device(self,new_device:InputDevice):
        self.mic.setoutputdevices(new_device)
        if self.running:
            self.mic.start(SAMPLE_RATE, BLOCK_SIZE)

    def change_volume(self,volume:float):
        self.mic.volume = volume

    def change_master_volume(self,volume:float):
        if volume < 0.0 or volume > 2.0:
            raise ValueError("Volume must be between 0.0 and 2.0")
        self.master_volume = volume

    def change_music_volume(self,volume:float):
        if volume < 0.0 or volume > 1.0:
            raise ValueError("Volume must be between 0.0 and 1.0")
        self.music_volume = volume

    def list_inputs(self) -> list[InputDevice]:
        return self.mic.listinput()

    def get_status(self):
        return self.status

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start_music(self,filepath:str):
        music = load_music(filepath)
        with self._music_lock:
            self._music = music
            self._music_pos = 0

    def stop_music(self):
        with self._music_lock:
            self._music = None

    def start(self):
        """Mikrofon es keverő szal inditasa; a kesz csomagok a self.buffer-be kerulnek."""
        if self.running:
            return
        self._stop.clear()
        self.mic.start(SAMPLE_RATE, BLOCK_SIZE)
        self._thread = threading.Thread(target=self._run, name="mixer", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        self.mic.stop()
        self.status = MixerStatus.IDLE
        self.level = 0.0

    def _next_music_block(self) -> np.ndarray | None:
        with self._music_lock:
            if self._music is None:
                return None
            block = self._music[self._music_pos:self._music_pos + BLOCK_SIZE]
            self._music_pos += BLOCK_SIZE
            if self._music_pos >= len(self._music):
                self._music = None
            if len(block) < BLOCK_SIZE:
                block = np.pad(block, (0, BLOCK_SIZE - len(block)))
            return block

    def _run(self):
        encoder = lameenc.Encoder()
        encoder.set_bit_rate(BITRATE_KBPS)
        encoder.set_in_sample_rate(SAMPLE_RATE)
        encoder.set_channels(1)
        encoder.set_quality(2)

        pending = b""
        packets = 0
        next_tick = time.monotonic()
        while not self._stop.is_set():
            music = self._next_music_block()
            mic = self.mic.read_block()

            status = MixerStatus.IDLE
            if music is not None:
                status |= MixerStatus.PLAYING_MUSIC
            if mic is not None and self.mic_volume > 0.0:
                status |= MixerStatus.PLAYING_MIC
            self.status = status

            pcm = mix_blocks(music, mic, self.music_volume, self.mic_volume, self.master_volume)
            self.level = max(int(np.abs(pcm.astype(np.int32)).max()) / 32767, self.level * LEVEL_DECAY)
            frames, pending = split_mp3_frames(pending + encoder.encode(pcm.tobytes()))
            for frame in frames:
                timestamp_ms = packets * BLOCK_SIZE * 1000 // SAMPLE_RATE
                self.buffer.publish(frame, timestamp_ms, status)
                packets += 1

            next_tick += BLOCK_SIZE / SAMPLE_RATE
            delay = next_tick - time.monotonic()
            if delay > 0:
                self._stop.wait(delay)
            elif delay < -0.5:
                next_tick = time.monotonic()  # nagy lemaradas: ujraszinkronizalas
