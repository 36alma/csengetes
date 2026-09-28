"""Hangfajl lejatszas kivalasztott kimeneti eszkozon (python-vlc / libvlc).

Biztonsag: csak helyi, engedelyezett konyvtarakban levo, engedelyezett kiterjesztesu
hangfajl jatszhato le; a VLC szigoru opciokkal indul (nincs video, lua, halozati
metaadat, felirat-automatika), es playlist/halozati tartalom sosem kovetheto.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger("csengetes")

try:  # a libvlc hianya ne akassza meg a szerver indulasat
    import vlc
    _VLC_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # ImportError vagy hianyzo libvlc.dll (OSError)
    vlc = None  # type: ignore[assignment]
    _VLC_IMPORT_ERROR = exc


class AudioPlayerError(Exception):
    pass


MAX_VOLUME_PERCENT = 200.0
MAX_FILE_BYTES = 200 * 1024 * 1024
WATCHDOG_MARGIN_S = 15.0
MAX_PLAY_SECONDS = 6 * 3600

ALLOWED_EXTENSIONS = frozenset(
    {".mp3", ".wav", ".ogg", ".oga", ".flac", ".m4a", ".aac", ".opus", ".wma"}
)

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ALLOWED_ROOTS = (
    os.path.join(_BASE_DIR, "media"),
    os.path.join(_BASE_DIR, "download"),
)

VLC_OPTIONS = (
    "--no-video",
    "--no-osd",
    "--no-video-title-show",
    "--no-sub-autodetect-file",
    "--no-lua",
    "--no-interact",
    "--no-metadata-network-access",
    "--no-stats",
    "--no-snapshot-preview",
    "--no-plugins-cache",
    "--quiet",
)


class _Session:
    """Egyetlen lejatszas allapota. A VLC callbackek csak az Event-eket allitjak."""

    def __init__(self, player):
        self.player = player
        self.done = threading.Event()
        self.error: str | None = None
        self.cancelled = False


class AudioPlayer:
    def __init__(self, allowed_roots=None, max_file_bytes: int = MAX_FILE_BYTES):
        self._device_index: int | None = None  # None = rendszer alapertelmezett
        self._volume_percent: float = 100.0  # 100 = eredeti hangero, 100 felett = boost
        self._max_file_bytes = max_file_bytes
        roots = DEFAULT_ALLOWED_ROOTS if allowed_roots is None else allowed_roots
        self._roots = [os.path.normcase(os.path.realpath(r)) for r in roots]

        self._lock = threading.RLock()  # allapot (instance, session, hangero)
        self._gate = threading.Lock()  # egyszerre egy lejatszas
        self._instance = None
        self._session: _Session | None = None

    # ---------- VLC peldany ----------

    def _get_instance(self):
        with self._lock:
            if self._instance is None:
                if vlc is None:
                    logger.error("A libvlc nem toltheto be: %s", _VLC_IMPORT_ERROR)
                    raise AudioPlayerError(
                        "A VLC lejatszo nem erheto el (telepitve van a VLC?)."
                    )
                try:
                    self._instance = vlc.Instance(*VLC_OPTIONS)
                except Exception as exc:
                    logger.error("VLC inicializalasi hiba: %s", exc)
                    raise AudioPlayerError("A VLC lejatszo nem indult el.") from exc
                if self._instance is None:
                    raise AudioPlayerError("A VLC lejatszo nem indult el.")
            return self._instance

    # ---------- eszkozok ----------

    def _enum_devices(self) -> list[tuple[str, str]]:
        """[(vlc_device_id, nev)] a VLC sorrendjeben."""
        player = self._get_instance().media_player_new()
        result: list[tuple[str, str]] = []
        head = player.audio_output_device_enum()
        try:
            node = head
            while node:
                dev = node.contents
                dev_id = (dev.device or b"").decode("utf-8", "replace")
                name = (dev.description or b"").decode("utf-8", "replace")
                result.append((dev_id, name))
                node = dev.next
        finally:
            if head:
                vlc.libvlc_audio_output_device_list_release(head)
            player.release()
        return result

    def list_output_devices(self) -> list[tuple[int, str]]:
        try:
            devices = self._enum_devices()
        except AudioPlayerError:
            return []
        except Exception as exc:
            logger.error("Eszkozlista hiba: %s", exc)
            return []
        return [(idx, name) for idx, (_id, name) in enumerate(devices)]

    def set_device(self, index: int | None) -> None:
        if index is not None:
            if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                raise AudioPlayerError("Ervenytelen eszkozindex.")
        self._device_index = index
        logger.info("Kimeneti eszkoz beallitva: %s", index)

    def get_device(self) -> int | None:
        return self._device_index

    def get_device_name(self) -> str | None:
        """A kivalasztott eszkoz neve (index helyett stabil azonosito), None = alapertelmezett."""
        if self._device_index is None:
            return None
        for idx, name in self.list_output_devices():
            if idx == self._device_index:
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

    def _resolve_device_id(self) -> str | None:
        if self._device_index is None:
            return None
        try:
            devices = self._enum_devices()
        except Exception as exc:
            logger.warning("Eszkozlista nem olvashato, alapertelmezett eszkoz: %s", exc)
            return None
        if self._device_index >= len(devices):
            logger.warning(
                "A(z) %s. kimeneti eszkoz nem letezik, alapertelmezett hasznalata.",
                self._device_index,
            )
            return None
        return devices[self._device_index][0]

    # ---------- hangero ----------

    def set_volume(self, percent: float) -> None:
        try:
            percent = float(percent)
        except (TypeError, ValueError):
            raise AudioPlayerError("Ervenytelen hangero.") from None
        if percent != percent:  # NaN
            raise AudioPlayerError("Ervenytelen hangero.")
        percent = max(0.0, min(MAX_VOLUME_PERCENT, percent))
        with self._lock:
            self._volume_percent = percent
            session = self._session
        if session is not None:
            session.player.audio_set_volume(int(round(percent)))
        logger.info("Hangero beallitva: %.0f%%", percent)

    def get_volume(self) -> float:
        return self._volume_percent

    # ---------- vezerles / allapot ----------

    def stop(self) -> None:
        with self._lock:
            session = self._session
        if session is not None:
            session.cancelled = True
            session.done.set()

    def pause(self) -> None:
        with self._lock:
            session = self._session
        if session is not None:
            session.player.set_pause(1)

    def resume(self) -> None:
        with self._lock:
            session = self._session
        if session is not None:
            session.player.set_pause(0)

    def is_playing(self) -> bool:
        with self._lock:
            session = self._session
        return session is not None and not session.done.is_set()

    def get_position_ms(self) -> int | None:
        with self._lock:
            session = self._session
        if session is None:
            return None
        pos = session.player.get_time()
        return pos if pos >= 0 else None

    def get_length_ms(self) -> int | None:
        with self._lock:
            session = self._session
        if session is None:
            return None
        length = session.player.get_length()
        return length if length > 0 else None

    # ---------- biztonsagi ellenorzes ----------

    def _validate_path(self, filepath) -> str:
        """Visszaadja a feloldott, engedelyezett fajl utvonalat, kulonben AudioPlayerError."""
        try:
            raw = os.fspath(filepath)
        except TypeError:
            raise AudioPlayerError("Ervenytelen fajlutvonal.") from None
        if isinstance(raw, bytes) or not raw or "\x00" in raw:
            raise AudioPlayerError("Ervenytelen fajlutvonal.")
        if "://" in raw or raw.startswith(("\\\\", "//")):
            logger.warning("Nem helyi utvonal elutasitva: %r", raw)
            raise AudioPlayerError("Csak helyi fajl jatszhato le.")

        real = os.path.realpath(raw)
        key = os.path.normcase(real)
        in_root = any(key == r or key.startswith(r + os.sep) for r in self._roots)
        if not in_root:
            logger.warning("Engedelyezett mappan kivuli fajl elutasitva: %s", real)
            raise AudioPlayerError("A fajl nem engedelyezett helyen van.")
        if Path(real).suffix.lower() not in ALLOWED_EXTENSIONS:
            logger.warning("Nem engedelyezett fajltipus elutasitva: %s", real)
            raise AudioPlayerError("Nem tamogatott fajltipus.")
        if not os.path.isfile(real):
            raise AudioPlayerError("A fajl nem talalhato.")
        try:
            size = os.path.getsize(real)
        except OSError:
            raise AudioPlayerError("A fajl nem olvashato.") from None
        if size <= 0 or size > self._max_file_bytes:
            logger.warning("Ervenytelen fajlmeret (%s bajt): %s", size, real)
            raise AudioPlayerError("A fajl merete nem megfelelo.")
        return real

    # ---------- lejatszas ----------

    def _prepare_session(self, real_path: str) -> tuple[_Session, float]:
        instance = self._get_instance()
        media = instance.media_new_path(real_path)  # helyi fajl, sosem nyers MRL
        if media is None:
            raise AudioPlayerError("A fajl nem nyithato meg.")
        media.add_option(":no-video")
        media.add_option(":no-sub-autodetect-file")
        media.parse()
        subitems = media.subitems()
        if subitems is not None and subitems.count() > 0:
            logger.warning("Osszetett/playlist tartalom elutasitva: %s", real_path)
            raise AudioPlayerError("Nem tamogatott fajltipus.")

        player = instance.media_player_new()
        player.set_media(media)
        session = _Session(player)

        def _finished(_event):
            session.done.set()

        def _errored(_event):
            session.error = "Lejatszasi hiba."
            session.done.set()

        events = player.event_manager()
        events.event_attach(vlc.EventType.MediaPlayerEndReached, _finished)
        events.event_attach(vlc.EventType.MediaPlayerEncounteredError, _errored)
        session._callbacks = (_finished, _errored)  # ne gyujtse be a GC

        device_id = self._resolve_device_id()
        if device_id is not None:
            player.audio_output_device_set(None, device_id)
        player.audio_set_volume(int(round(self._volume_percent)))
        duration_ms = media.get_duration()
        return session, (duration_ms / 1000.0 if duration_ms and duration_ms > 0 else 0.0)

    def _run_session(self, real_path: str) -> None:
        session, duration_s = self._prepare_session(real_path)
        try:
            with self._lock:
                self._session = session
            if session.player.play() == -1:
                raise AudioPlayerError("A lejatszas nem indult el.")
            timeout = min(MAX_PLAY_SECONDS, duration_s + WATCHDOG_MARGIN_S) if duration_s else MAX_PLAY_SECONDS
            if not session.done.wait(timeout):
                logger.error("Lejatszas idotullepes: %s", real_path)
                raise AudioPlayerError("A lejatszas idotullepes miatt megszakadt.")
            if session.error:
                raise AudioPlayerError(session.error)
        finally:
            with self._lock:
                if self._session is session:
                    self._session = None
            try:
                session.player.stop()
            finally:
                session.player.release()

    def play(
        self,
        filepath: str,
        blocking: bool = False,
        force: bool = False,
        on_done=None,
    ) -> None:
        """Lejatssza a megadott hangfajlt. Alapertelmezetten nem blokkolo (kulon szalon fut).

        force=True eseten a lejatszas eloszor megszakitja a folyamatban levo hangot,
        force=False eseten megvarja annak a veget.
        on_done(error: str | None) - ha meg van adva, a lejatszas vegen (siker vagy hiba
        eseten is) meghivodik a lejatszo szalon; a hivonak kell a fo szalra atterelnie
        (pl. GUI.after(0, ...)) ha az UI-t erinti.
        """

        def _do_play():
            error = None
            try:
                real_path = self._validate_path(filepath)
                if force:
                    self.stop()
                with self._gate:
                    self._run_session(real_path)
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
