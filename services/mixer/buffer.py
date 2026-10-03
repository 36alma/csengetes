import threading
from collections import deque

from model import AudioPacket, MixerStatus
from model.audio import SEQ_MODULO


class PacketBuffer:
    """Gyuruspuffer: egy iro (a mixer), tobb olvaso.

    Minden olvaso a sajat poziciojat koveti (abszolut, nem korbeforduló szam),
    igy a lemaradó olvaso az elso meg meglevo csomagra ugrik, a keses nem no.
    """

    def __init__(self, capacity: int = 256):
        self._packets: deque[AudioPacket] = deque(maxlen=capacity)
        self._head = 0  # a kovetkezo publikalt csomag abszolut pozicioja
        self._cond = threading.Condition()

    @property
    def head(self) -> int:
        """A stream jelenlegi vege: ide csatlakozik, aki menet kozben jon be."""
        with self._cond:
            return self._head

    def publish(self, payload: bytes, timestamp_ms: int, status: MixerStatus) -> AudioPacket:
        with self._cond:
            packet = AudioPacket(self._head % SEQ_MODULO, timestamp_ms, status, payload)
            self._packets.append(packet)
            self._head += 1
            self._cond.notify_all()
            return packet

    def read(self, position: int, timeout: float | None = None) -> tuple[AudioPacket, int] | None:
        """A `position`-tol az elso elerheto csomag es a kovetkezo pozicio.

        None, ha a timeout alatt nem erkezett uj csomag.
        """
        with self._cond:
            if not self._cond.wait_for(lambda: position < self._head, timeout):
                return None
            oldest = self._head - len(self._packets)
            position = max(position, oldest)
            return self._packets[position - oldest], position + 1
