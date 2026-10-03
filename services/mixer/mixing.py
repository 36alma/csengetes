import numpy as np

SAMPLE_RATE = 44100
BLOCK_SIZE = 1152  # egy MPEG-1 Layer III keret mintaszama


def mix_blocks(
    music: np.ndarray | None,
    mic: np.ndarray | None,
    music_volume: float,
    mic_volume: float,
    master_volume: float,
) -> np.ndarray:
    """Ket float32 blokk osszekeverese int16 PCM-be. Ha egy forras nincs, csend."""
    mixed = np.zeros(BLOCK_SIZE, dtype=np.float32)
    if music is not None:
        mixed += music * music_volume
    if mic is not None:
        mixed += mic * mic_volume
    mixed *= master_volume
    np.clip(mixed, -1.0, 1.0, out=mixed)
    return (mixed * 32767).astype(np.int16)


_BITRATES = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
_SAMPLE_RATES = [44100, 48000, 32000]


def split_mp3_frames(data: bytes) -> tuple[list[bytes], bytes]:
    """MPEG-1 Layer III keretekre bontas. Visszaadja a kereteket es a maradekot."""
    frames: list[bytes] = []
    pos = 0
    while pos + 4 <= len(data):
        b1, b2 = data[pos + 1], data[pos + 2]
        valid = (
            data[pos] == 0xFF
            and (b1 & 0xFE) == 0xFA  # sync, MPEG-1, Layer III
            and (b2 >> 4) not in (0, 15)
            and ((b2 >> 2) & 3) != 3
        )
        if not valid:
            pos += 1  # nem keret eleje (pl. kodolo fejlec), atlepjuk
            continue
        bitrate = _BITRATES[b2 >> 4] * 1000
        sample_rate = _SAMPLE_RATES[(b2 >> 2) & 3]
        length = 144 * bitrate // sample_rate + ((b2 >> 1) & 1)
        if pos + length > len(data):
            break
        frames.append(data[pos:pos + length])
        pos += length
    return frames, data[pos:]
