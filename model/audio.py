import struct
from dataclasses import dataclass

from .mixer import MixerStatus

PACKET_MAGIC = b"CSGP"
# magic, seq, timestamp_ms, status, payload hossz (network byte order)
_HEADER = struct.Struct("!4sIIBH")
HEADER_SIZE = _HEADER.size
SEQ_MODULO = 1 << 32


@dataclass(frozen=True)
class AudioPacket:
    """Egy MP3-keret sorszammal: UDP-n ebbol latszik a vesztes / atrendezodes."""
    seq: int
    timestamp_ms: int
    status: MixerStatus
    payload: bytes

    def to_bytes(self) -> bytes:
        header = _HEADER.pack(
            PACKET_MAGIC,
            self.seq % SEQ_MODULO,
            self.timestamp_ms % SEQ_MODULO,
            self.status.value,
            len(self.payload),
        )
        return header + self.payload

    @classmethod
    def from_bytes(cls, data: bytes) -> "AudioPacket":
        if len(data) < HEADER_SIZE:
            raise ValueError("A csomag rovidebb, mint a fejlec")
        magic, seq, timestamp_ms, status, length = _HEADER.unpack_from(data)
        if magic != PACKET_MAGIC:
            raise ValueError("Ismeretlen csomag (rossz magic)")
        payload = data[HEADER_SIZE:]
        if len(payload) != length:
            raise ValueError("A csomag hossza nem egyezik a fejlecben levovel")
        return cls(seq, timestamp_ms, MixerStatus(status), payload)
