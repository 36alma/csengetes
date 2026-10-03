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


class MixerPlayer:
    """Egy hangforras (`kind`: "bell" vagy "music") lejatszoja.

    A ket peldany kozos `gate`-et hasznal: egyszerre egy hang szol, a force=True
    megszakitja a futot, a force=False megvarja a veget.
    """

    def __init__(
        self,
        mixer: "Mixer",
        kind: str,
        gate: threading.Lock | None = None,
        allowed_roots=None,
        max_file_bytes: int = MAX_FILE_BYTES,
        device_lister: Callable[[], list[tuple[int, str]]] = list_output_devices,
    ):
        self._mixer = mixer
        self._kind = kind
        self._gate = gate if gate is not None else threading.Lock()
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
        self._mixer.stop_file()

    def is_playing(self) -> bool:
        return self._mixer.file_playing

    def _run(self, real_path: str) -> None:
        if not self._mixer.running:
            raise AudioPlayerError("A mixer nem fut.")
        playback = self._mixer.play_file(real_path, self._kind)
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
                if force:
                    self._mixer.stop_file()
                with self._gate:
                    self._run(real_path)
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
