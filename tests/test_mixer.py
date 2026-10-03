import os
import sys
import threading

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from model import AudioPacket, MixerStatus
from model.audio import HEADER_SIZE
from services.mixer import Mixer, PacketBuffer
from services.mixer.mixing import BLOCK_SIZE, SAMPLE_RATE, mix_blocks, split_mp3_frames


class FakeMic:
    """Mic helyettes: allando szinuszt ad, hardver nelkul."""

    def __init__(self):
        self.volume = 1.0
        self.current_output_device = None
        t = np.arange(BLOCK_SIZE) / SAMPLE_RATE
        self._block = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

    def start(self, samplerate, blocksize):
        pass

    def stop(self):
        pass

    def read_block(self):
        return self._block

    def setoutputdevices(self, device):
        self.current_output_device = device


# ---- AudioPacket ----

def test_packet_roundtrip():
    packet = AudioPacket(7, 1234, MixerStatus.MIXING, b"\xff\xfb\x90abc")
    assert AudioPacket.from_bytes(packet.to_bytes()) == packet


def test_packet_seq_wraps_on_wire():
    packet = AudioPacket((1 << 32) + 5, 0, MixerStatus.IDLE, b"x")
    assert AudioPacket.from_bytes(packet.to_bytes()).seq == 5


def test_packet_rejects_bad_data():
    good = AudioPacket(1, 0, MixerStatus.IDLE, b"abc").to_bytes()
    with pytest.raises(ValueError):
        AudioPacket.from_bytes(good[:HEADER_SIZE - 1])
    with pytest.raises(ValueError):
        AudioPacket.from_bytes(b"XXXX" + good[4:])
    with pytest.raises(ValueError):
        AudioPacket.from_bytes(good[:-1])


# ---- PacketBuffer ----

def test_buffer_sequential_read():
    buf = PacketBuffer(capacity=4)
    for i in range(3):
        buf.publish(bytes([i]), i, MixerStatus.IDLE)
    pos = 0
    seqs = []
    for _ in range(3):
        packet, pos = buf.read(pos, timeout=0.1)
        seqs.append(packet.seq)
    assert seqs == [0, 1, 2]
    assert buf.read(pos, timeout=0.01) is None


def test_buffer_lagging_reader_skips_to_oldest():
    buf = PacketBuffer(capacity=3)
    for i in range(10):
        buf.publish(b"x", i, MixerStatus.IDLE)
    packet, pos = buf.read(0, timeout=0.1)
    assert packet.seq == 7
    assert pos == 8


def test_buffer_join_at_head_gets_only_new_packets():
    buf = PacketBuffer()
    buf.publish(b"old", 0, MixerStatus.IDLE)
    pos = buf.head
    threading.Timer(0.05, lambda: buf.publish(b"new", 1, MixerStatus.IDLE)).start()
    packet, _ = buf.read(pos, timeout=1)
    assert packet.payload == b"new"


# ---- keveres ----

def _block(value):
    return np.full(BLOCK_SIZE, value, dtype=np.float32)


def test_mix_sums_and_applies_volumes():
    out = mix_blocks(_block(0.2), _block(0.2), 1.0, 0.5, 1.0)
    assert out[0] == int(0.3 * 32767)
    out = mix_blocks(_block(0.2), _block(0.2), 1.0, 0.5, 2.0)
    assert out[0] == int(0.6 * 32767)


def test_mix_clips():
    out = mix_blocks(_block(0.9), _block(0.9), 1.0, 1.0, 2.0)
    assert out.max() == 32767 and out.min() == 32767


def test_mix_missing_sources_is_silence():
    assert not mix_blocks(None, None, 1.0, 1.0, 1.0).any()
    out = mix_blocks(None, _block(0.5), 1.0, 1.0, 1.0)
    assert out[0] == int(0.5 * 32767)


def test_split_mp3_frames_keeps_remainder():
    frame = b"\xff\xfb\x90\x00" + bytes(413)  # 128 kbps, 44.1 kHz, padding nelkul: 417 bajt
    assert len(frame) == 417
    frames, rest = split_mp3_frames(b"junk" + frame + frame + frame[:100])
    assert frames == [frame, frame]
    assert rest == frame[:100]


# ---- Mixer vegig ----

def _collect(buf, count, timeout=3.0):
    pos, packets = 0, []
    while len(packets) < count:
        item = buf.read(pos, timeout=timeout)
        assert item is not None, "a mixer nem adott elég csomagot"
        packet, pos = item
        packets.append(packet)
    return packets


def test_mixer_emits_numbered_mp3_frames():
    mixer = Mixer(mic=FakeMic())
    mixer.start()
    try:
        packets = _collect(mixer.buffer, 12)
    finally:
        mixer.stop()
    assert [p.seq for p in packets] == list(range(packets[0].seq, packets[0].seq + 12))
    assert all(p.timestamp_ms >= 0 for p in packets)
    for p in packets:
        frames, rest = split_mp3_frames(p.payload)
        assert len(frames) == 1 and rest == b"", "egy csomag pontosan egy MP3-keret"
    assert any(p.status & MixerStatus.PLAYING_MIC for p in packets)
    assert mixer.get_status() == MixerStatus.IDLE


def test_mixer_music_sets_mixing_status(tmp_path):
    wav = tmp_path / "t.wav"
    sf.write(wav, 0.2 * np.sin(np.linspace(0, 2000, SAMPLE_RATE)), 22050)  # mas mintavetel, resample
    mixer = Mixer(mic=FakeMic())
    mixer.start_music(str(wav))
    mixer.start()
    try:
        packets = _collect(mixer.buffer, 12)
    finally:
        mixer.stop()
    assert any(p.status == MixerStatus.MIXING for p in packets)


def test_mixer_volume_validation():
    mixer = Mixer(mic=FakeMic())
    with pytest.raises(ValueError):
        mixer.change_master_volume(2.5)
    with pytest.raises(ValueError):
        mixer.change_music_volume(1.5)
    mixer.change_master_volume(1.5)
    assert mixer.master_volume == 1.5


def test_mixer_level_follows_signal_and_resets():
    mixer = Mixer(mic=FakeMic())
    assert mixer.level == 0.0
    mixer.start()
    try:
        _collect(mixer.buffer, 6)
        assert 0.2 < mixer.level <= 1.0  # 0.3 amplitudoju szinusz
        mixer.change_volume(0.0)
        _collect(mixer.buffer, 40)  # a lecsengeshez par blokk kell
        assert mixer.level < 0.01
    finally:
        mixer.stop()
    assert mixer.level == 0.0
