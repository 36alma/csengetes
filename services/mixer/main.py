import logging
import threading
import time

import lameenc
import numpy as np
import soundfile as sf

from model import InputDevice, MixerStatus
from services.mic import Mic
from .buffer import PacketBuffer
from .errors import AudioPlayerError
from .mixing import BLOCK_SIZE, SAMPLE_RATE, mix_blocks, split_mp3_frames

logger = logging.getLogger("csengetes")

BITRATE_KBPS = 128
LEVEL_DECAY = 0.85  # blokkonkenti lecsengés (~26 ms): a jelszint nem ugral, de gyorsan elhal
KIND_FLAGS = {"music": MixerStatus.PLAYING_MUSIC, "bell": MixerStatus.BELL}
OUTPUT_RETRY_S = 5.0  # kiesett/meg nem nyithato helyi kimenet ujraprobalasa ennyi masodpercenkent


MAX_FILE_SECONDS = 2 * 3600  # ennel hosszabb fajlt nem dekodolunk (memoria)
DECODE_BLOCK_FRAMES = 32768  # ennyi forrasmintat dekodolunk egyszerre


def load_music(filepath: str) -> np.ndarray:
    """Zene betoltese mono, SAMPLE_RATE-es float32 tombbe.

    Darabonkent dekodol egy elore lefoglalt kimeneti tombbe, igy a csucs memoria az
    eredmeny meretenek kis tobbszorose. Az atmintazas linearis interpolacio
    (mint np.interp a teljes fajlon); a darabhatarokon az elozo darab utolso mintaja
    atfedeskent megmarad, igy nincs szakadas.
    """
    info = sf.info(filepath)
    if info.samplerate <= 0 or info.frames / info.samplerate > MAX_FILE_SECONDS:
        raise AudioPlayerError("A fajl tul hosszu.")

    with sf.SoundFile(filepath) as f:
        rate, total = f.samplerate, f.frames
        target = total if rate == SAMPLE_RATE else int(total * SAMPLE_RATE / rate)
        out = np.empty(max(target, 0), dtype=np.float32)
        # a j. kimeneti minta forraspozicioja: j * step (np.linspace(0, total - 1, target))
        step = (total - 1) / (target - 1) if target > 1 else 0.0
        filled = 0  # ennyi kimeneti minta kesz
        base = 0  # a kovetkezo darab elso mintajanak forrasindexe
        prev = np.empty(0, dtype=np.float32)  # atfedes: az elozo darab utolso mintaja
        for block in f.blocks(blocksize=DECODE_BLOCK_FRAMES, dtype="float32", always_2d=True):
            if filled >= target:
                break
            mono = block[:, 0] if block.shape[1] == 1 else block.mean(axis=1, dtype=np.float32)
            if rate == SAMPLE_RATE:
                count = min(len(mono), target - filled)
                out[filled:filled + count] = mono[:count]
                filled += count
                continue
            buf = np.concatenate([prev, mono])
            buf_start = base - len(prev)
            last = base + len(mono) - 1  # az eddig ismert utolso forrasindex
            if step > 0:
                end = min(target, int(np.floor(last / step + 1e-6)) + 1)
            else:
                end = min(target, 1)
            if end > filled:
                positions = np.arange(filled, end, dtype=np.float64) * step - buf_start
                out[filled:end] = np.interp(positions, np.arange(len(buf)), buf)
                filled = end
            prev = mono[-1:].copy()
            base += len(mono)
    if filled < len(out):
        # a fejlec tobb mintat igert, mint amennyi volt (pl. MP3): a vege levagva
        out = out[:filled].copy()
    return out


class Playback:
    """Egy fajl lejatszasanak allapota: a hivo a `done` esemenyre var."""

    def __init__(self, kind: str, duration_s: float):
        self.kind = kind
        self.duration_s = duration_s
        self.done = threading.Event()
        self.error: str | None = None
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True
        self.done.set()


class Mixer():
    def __init__(self, mic: Mic | None = None, buffer: PacketBuffer | None = None, output_factory=None):
        self.mic = mic if mic is not None else Mic()
        self.buffer = buffer if buffer is not None else PacketBuffer()
        self.master_volume = 1.0
        self.music_volume = 1.0
        self.status = MixerStatus.IDLE
        self.level = 0.0  # kimeneti csucsertek 0..1, blokkonkent lecsengetve (a feluleti meronek)
        self._file: np.ndarray | None = None
        self._file_pos = 0
        self._playback: Playback | None = None
        self._file_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._mic_live = False
        self._mic_lock = threading.Lock()  # set_mic_live / change_output_device / stop sorositasa
        self._output_factory = output_factory  # Callable[[int | None], sink] | None
        self.output_device_index: int | None = None  # None = rendszer alapertelmezett
        self.output_ok = True
        self._output_dirty = threading.Event()
        self._last_output_error: tuple | None = None  # az utoljara naplozott megnyitasi hiba

    @property
    def current_output_device(self):
        return self.mic.current_output_device

    @property
    def mic_volume(self) -> float:
        return self.mic.volume

    def change_output_device(self,new_device:InputDevice):
        """Bemeneti (mikrofon) eszkoz valtasa; elo mikrofonnal az uj eszkozon ujranyit.

        Ha az ujranyitas elbukik, a mikrofon kikapcsolt allapotba kerul es a kivetel tovabbmegy.
        """
        with self._mic_lock:
            self.mic.setoutputdevices(new_device)
            if not self._mic_live:
                return
            try:
                self.mic.start(SAMPLE_RATE, BLOCK_SIZE)
            except Exception:
                self._mic_live = False
                self._stop_mic_quietly()
                raise

    def _stop_mic_quietly(self) -> None:
        try:
            self.mic.stop()
        except Exception as exc:
            logger.warning("A mikrofon leallitasa sikertelen: %s", exc)

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

    def set_output_device(self, index: int | None) -> None:
        """Kimeneti eszkoz valtas: a keveroszal a kovetkezo blokk elott ujranyitja."""
        self.output_device_index = index
        self._output_dirty.set()

    def _open_sink(self):
        """A kimeneti folyam megnyitasa; hiba eseten None.

        Ugyanazt a (valtozatlan) hibat csak egyszer naplozza, hogy az ujraprobalkozas
        ne tomje a naplot.
        """
        if self._output_factory is None:
            return None
        index = self.output_device_index
        try:
            sink = self._output_factory(index)
        except Exception as exc:
            error = (index, type(exc).__name__, str(exc))
            if error != self._last_output_error:
                logger.error("A kimeneti eszkoz nem nyithato meg (%s): %s", index, exc)
                self._last_output_error = error
            self.output_ok = False
            return None
        if self._last_output_error is not None:
            logger.info("A kimeneti eszkoz ujra elerheto (%s)", index)
            self._last_output_error = None
        self.output_ok = True
        return sink

    @staticmethod
    def _close_sink(sink) -> None:
        if sink is None:
            return
        try:
            sink.close()
        except Exception as exc:
            logger.warning("A kimeneti folyam lezarasa sikertelen: %s", exc)

    def list_inputs(self) -> list[InputDevice]:
        return self.mic.listinput()

    def get_status(self):
        return self.status

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def file_playing(self) -> bool:
        return self._playback is not None

    @property
    def mic_live(self) -> bool:
        return self._mic_live

    def set_mic_live(self, live: bool) -> None:
        """Mikrofon be/ki: kikapcsolva a mikrofonfolyam sincs megnyitva."""
        live = bool(live)
        with self._mic_lock:
            if live == self._mic_live:
                return
            if live:
                try:
                    self.mic.start(SAMPLE_RATE, BLOCK_SIZE)
                except Exception:
                    self._stop_mic_quietly()  # felig megnyitott folyam se maradjon
                    raise
            else:
                self.mic.stop()
            self._mic_live = live

    def decode_file(self, filepath: str) -> np.ndarray:
        """A fajl dekodolasa mono, SAMPLE_RATE-es float32 tombbe (lassu lehet: zar nelkul fut)."""
        try:
            return load_music(filepath)
        except AudioPlayerError:
            raise
        except Exception as exc:
            logger.error("A fajl nem dekodolhato (%s): %s", filepath, exc)
            raise AudioPlayerError("A fajl nem olvashato.") from exc

    def play_samples(self, samples: np.ndarray, kind: str) -> Playback:
        """A mar dekodolt mintak beallitasa forraskent; a korabbi lejatszast megszakitja."""
        if kind not in KIND_FLAGS:
            raise ValueError(f"Ismeretlen forrastipus: {kind}")
        playback = Playback(kind, len(samples) / SAMPLE_RATE)
        with self._file_lock:
            if self._playback is not None:
                self._playback.cancel()
            self._file = samples
            self._file_pos = 0
            self._playback = playback
        return playback

    def play_file(self, filepath: str, kind: str) -> Playback:
        """Dekodolja es forraskent beallitja a fajlt; a korabbi lejatszast megszakitja."""
        if kind not in KIND_FLAGS:
            raise ValueError(f"Ismeretlen forrastipus: {kind}")
        return self.play_samples(self.decode_file(filepath), kind)

    def stop_file(self, playback: Playback | None = None, kind: str | None = None) -> None:
        """A megadott (vagy ha None, az aktualis) lejatszas megszakitasa.

        kind megadasa eseten csak az adott tipusu ("bell"/"music") lejatszast allitja le.
        """
        with self._file_lock:
            current = self._playback
            if current is None or (playback is not None and playback is not current):
                return
            if kind is not None and current.kind != kind:
                return
            current.cancel()
            self._file = None
            self._playback = None

    def start(self):
        """Keverő szal inditasa; a kesz csomagok a self.buffer-be kerulnek."""
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="mixer", daemon=True)
        self._thread.start()

    def stop(self):
        """A keveroszal leallitasa; a lejatszas, az allapot es a jelszint mindenkepp visszaall.

        Ha a szal 2 mp alatt sem all le, figyelmeztetes es tovabblepes; a referenciat
        megtartjuk, hogy egy ujabb start() ne inditson masodik keveroszalat mellette.
        """
        self._stop.set()
        try:
            if self._thread is not None:
                self._thread.join(timeout=2)
                if self._thread.is_alive():
                    logger.warning("A keveroszal nem allt le 2 mp alatt, tovabblepes")
                else:
                    self._thread = None
            self.set_mic_live(False)
        finally:
            self.stop_file()
            self.status = MixerStatus.IDLE
            self.level = 0.0

    def _next_file_block(self) -> tuple[np.ndarray | None, MixerStatus]:
        with self._file_lock:
            if self._file is None:
                return None, MixerStatus.IDLE
            block = self._file[self._file_pos:self._file_pos + BLOCK_SIZE]
            self._file_pos += BLOCK_SIZE
            flag = KIND_FLAGS[self._playback.kind]
            if self._file_pos >= len(self._file):
                self._playback.done.set()
                self._file = None
                self._playback = None
            if len(block) < BLOCK_SIZE:
                block = np.pad(block, (0, BLOCK_SIZE - len(block)))
            return block, flag

    def _run(self):
        encoder = lameenc.Encoder()
        encoder.set_bit_rate(BITRATE_KBPS)
        encoder.set_in_sample_rate(SAMPLE_RATE)
        encoder.set_channels(1)
        encoder.set_quality(2)

        # a start() elott beallitott eszkozt (set_output_device) mar ez a megnyitas kezeli
        self._output_dirty.clear()
        sink = self._open_sink()
        next_retry = time.monotonic() + OUTPUT_RETRY_S
        pending = b""
        packets = 0
        next_tick = time.monotonic()
        try:
            while not self._stop.is_set():
                try:
                    if self._output_dirty.is_set():
                        self._output_dirty.clear()
                        self._last_output_error = None  # kezi valtas: a hiba ujra naplozhato
                        self._close_sink(sink)
                        sink = self._open_sink()
                        next_retry = time.monotonic() + OUTPUT_RETRY_S
                        next_tick = time.monotonic()
                    elif sink is None and self._output_factory is not None and time.monotonic() >= next_retry:
                        # automatikus ujranyitas (nincs visszaeses az alapertelmezett eszkozre)
                        sink = self._open_sink()
                        next_retry = time.monotonic() + OUTPUT_RETRY_S
                        next_tick = time.monotonic()

                    music, file_flag = self._next_file_block()
                    mic = self.mic.read_block() if self._mic_live else None

                    status = file_flag
                    if mic is not None and self.mic_volume > 0.0:
                        status |= MixerStatus.PLAYING_MIC
                    self.status = status

                    # a csengo hangereje nem fugg a zene csuszkatol
                    file_volume = self.music_volume if file_flag == MixerStatus.PLAYING_MUSIC else 1.0
                    pcm = mix_blocks(music, mic, file_volume, self.mic_volume, self.master_volume)
                    self.level = max(int(np.abs(pcm.astype(np.int32)).max()) / 32767, self.level * LEVEL_DECAY)

                    paced_by_output = False
                    if sink is not None:
                        try:
                            sink.write(pcm)  # blokkol: a hardver orajele adja a tempot
                            paced_by_output = True
                        except Exception as exc:
                            logger.error("A helyi kimenet kiesett: %s", exc)
                            self.output_ok = False
                            self._close_sink(sink)
                            sink = None
                            next_retry = time.monotonic() + OUTPUT_RETRY_S

                    frames, pending = split_mp3_frames(pending + encoder.encode(pcm.tobytes()))
                    for frame in frames:
                        timestamp_ms = packets * BLOCK_SIZE * 1000 // SAMPLE_RATE
                        self.buffer.publish(frame, timestamp_ms, status)
                        packets += 1

                    if paced_by_output:
                        next_tick = time.monotonic()
                        continue
                    next_tick += BLOCK_SIZE / SAMPLE_RATE
                    delay = next_tick - time.monotonic()
                    if delay > 0:
                        self._stop.wait(delay)
                    elif delay < -0.5:
                        next_tick = time.monotonic()  # nagy lemaradas: ujraszinkronizalas
                except Exception:
                    # a szal nem halhat meg: naplozzuk, kicsit varunk (ne porogjon), folytatjuk
                    logger.exception("Varatlan hiba a keveroszalban")
                    self._stop.wait(0.1)
                    next_tick = time.monotonic()
        finally:
            self._close_sink(sink)
