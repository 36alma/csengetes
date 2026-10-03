"""Az AudioPlayer felulete a mixer folott: a lejatszas a kozos keveresbe kerul."""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Callable

from .errors import AudioPlayerError
from .output import list_output_devices
from .paths import MAX_FILE_BYTES, normalize_roots, validate_audio_path

if TYPE_CHECKING:
    from .main import Mixer

logger = logging.getLogger("csengetes")

MAX_VOLUME_PERCENT = 200.0
WATCHDOG_MARGIN_S = 15.0
MAX_PLAY_SECONDS = 6 * 3600

# force=True eseten mely forrastipusokat szakit meg a kero: a csengo mindent,
# a zene csak zenet (csengo alatt a zene megvarja a csengo veget)
PREEMPTS = {"bell": ("bell", "music"), "music": ("music",)}


class PlayGate:
    """A lejatszok kozos kapuja: egyszerre egy hang szol.

    acquire(force=True): elonyt kap minden nem-force-os keressel szemben (amig var
    force-os kero, nem-force-os nem jut be), es a `preempts` tipusu aktualis
    lejatszast megszakitja - akkor is, ha az csak a varakozas kozben indul el.
    """

    def __init__(self):
        self._cond = threading.Condition()
        self._held = False
        self._force_waiting = 0
        self._preempt: dict[str, int] = {}  # tipus -> ennyi varakozo force-os kero szakitja meg
        self._current: tuple[str, Callable[[], None]] | None = None

    def acquire(self, force: bool = False, preempts: tuple[str, ...] = ()) -> None:
        with self._cond:
            if not force:
                while self._held or self._force_waiting:
                    self._cond.wait()
                self._held = True
                return
            self._force_waiting += 1
            for kind in preempts:
                self._preempt[kind] = self._preempt.get(kind, 0) + 1
            try:
                if self._current is not None and self._current[0] in preempts:
                    self._current[1]()
                while self._held:
                    self._cond.wait()
                self._held = True
            finally:
                self._force_waiting -= 1
                for kind in preempts:
                    self._preempt[kind] -= 1
                    if not self._preempt[kind]:
                        del self._preempt[kind]
                self._cond.notify_all()  # a nem-force-os varakozok ujra ellenorizzenek

    def attach(self, kind: str, cancel: Callable[[], None]) -> None:
        """A kapu birtokosa bejelenti a lejatszasat; ha mar var ra megszakito kero, azonnal leall."""
        with self._cond:
            self._current = (kind, cancel)
            preempted = bool(self._preempt.get(kind))
        if preempted:
            cancel()

    def release(self) -> None:
        with self._cond:
            self._held = False
            self._current = None
            self._cond.notify_all()


class MixerPlayer:
    """Egy hangforras (`kind`: "bell" vagy "music") lejatszoja.

    A ket peldany kozos `gate`-et (PlayGate) hasznal: egyszerre egy hang szol. force=True:
    a csengo minden hangot megszakit, a zene csak zenet (a csengot megvarja); a force-os
    kero megelozi a sorban allo nem-force-os kereseket. force=False: megvarja a veget.
    """

    def __init__(
        self,
        mixer: "Mixer",
        kind: str,
        gate: PlayGate | None = None,
        allowed_roots=None,
        max_file_bytes: int = MAX_FILE_BYTES,
        device_lister: Callable[[], list[tuple[int, str]]] = list_output_devices,
    ):
        self._mixer = mixer
        self._kind = kind
        self._gate = gate if gate is not None else PlayGate()
        self._roots = normalize_roots(allowed_roots)
        self._max_file_bytes = max_file_bytes
        self._device_lister = device_lister

    # ---------- eszkozok ----------

    def list_output_devices(self) -> list[tuple[int, str]]:
        try:
            return self._device_lister()
        except Exception as exc:
            logger.error("Eszkozlista hiba: %s", exc)
            return []

    def set_device(self, index: int | None) -> None:
        if index is not None:
            if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                raise AudioPlayerError("Ervenytelen eszkozindex.")
        self._mixer.set_output_device(index)
        logger.info("Kimeneti eszkoz beallitva: %s", index)

    def get_device(self) -> int | None:
        return self._mixer.output_device_index

    def get_device_name(self) -> str | None:
        """A kivalasztott eszkoz neve (index helyett stabil azonosito), None = alapertelmezett."""
        index = self.get_device()
        if index is None:
            return None
        for idx, name in self.list_output_devices():
            if idx == index:
                return name
        return None

    def find_device_index(self, name: str | None) -> int | None:
        """Nev alapjan megkeresi a jelenlegi indexet (None, ha nincs ilyen eszkoz)."""
        if not name:
            return None
        for idx, dev_name in self.list_output_devices():
            if dev_name == name:
                return idx
        return None

    # ---------- hangero ----------

    def set_volume(self, percent: float) -> None:
        try:
            percent = float(percent)
        except (TypeError, ValueError):
            raise AudioPlayerError("Ervenytelen hangero.") from None
        if percent != percent:  # NaN
            raise AudioPlayerError("Ervenytelen hangero.")
        percent = max(0.0, min(MAX_VOLUME_PERCENT, percent))
        self._mixer.change_master_volume(percent / 100.0)
        logger.info("Hangero beallitva: %.0f%%", percent)

    def get_volume(self) -> float:
        return self._mixer.master_volume * 100.0

    # ---------- vezerles ----------

    def stop(self) -> None:
        """Csak a sajat tipusu (bell/music) lejatszast allitja le."""
        self._mixer.stop_file(kind=self._kind)

    def is_playing(self) -> bool:
        """Szol-e barmilyen fajl a mixerben (csengo vagy zene, nem csak a sajat tipus)."""
        return self._mixer.file_playing

    def _run(self, real_path: str, samples) -> None:
        playback = self._mixer.play_samples(samples, self._kind)
        self._gate.attach(self._kind, lambda: self._mixer.stop_file(playback))
        timeout = min(MAX_PLAY_SECONDS, playback.duration_s + WATCHDOG_MARGIN_S)
        if not playback.done.wait(timeout):
            self._mixer.stop_file(playback)
            logger.error("Lejatszas idotullepes: %s", real_path)
            raise AudioPlayerError("A lejatszas idotullepes miatt megszakadt.")
        if playback.error:
            raise AudioPlayerError(playback.error)

    def play(
        self,
        filepath: str,
        blocking: bool = False,
        force: bool = False,
        on_done=None,
    ) -> None:
        """Lejatssza a hangfajlt. Alapertelmezetten nem blokkolo (kulon szalon fut).

        force=True: eloszor megszakitja a folyamatban levo hangot; force=False: megvarja a veget.
        on_done(error: str | None): a lejatszas vegen (siker vagy hiba eseten is) hivodik a
        lejatszo szalon. Ha nincs on_done, a hiba AudioPlayerError-kent kivetelt dob.
        """

        def _do_play():
            error = None
            try:
                real_path = validate_audio_path(filepath, self._roots, self._max_file_bytes)
                if not self._mixer.running:
                    raise AudioPlayerError("A mixer nem fut.")
                # a (lassu) dekodolas a kapun kivul: ne tartson fel egy kozben erkezo csengot
                samples = self._mixer.decode_file(real_path)
                self._gate.acquire(force=force, preempts=PREEMPTS[self._kind] if force else ())
                try:
                    if not self._mixer.running:
                        raise AudioPlayerError("A mixer nem fut.")
                    self._run(real_path, samples)
                finally:
                    self._gate.release()
                logger.info("Lejatszas kesz: %s", real_path)
            except AudioPlayerError as exc:
                logger.error("Hiba a lejatszas soran: %s", exc)
                error = str(exc)
            except Exception as exc:
                logger.error("Varatlan hiba a lejatszas soran (%s): %s", filepath, exc)
                error = "Lejatszasi hiba."
            finally:
                if on_done is not None:
                    on_done(error)
            if error is not None and on_done is None:
                raise AudioPlayerError(error)

        if blocking:
            _do_play()
        else:
            threading.Thread(target=_do_play, daemon=True).start()
