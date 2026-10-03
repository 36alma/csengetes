import logging
import os
import sys
import threading
import time

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fakes import FakeMic, FakeSink, wait_until
from model import AudioPacket, InputDevice, MixerStatus
from model.audio import HEADER_SIZE
from services.mixer import Mixer, PacketBuffer
from services.mixer.errors import AudioPlayerError
import services.mixer.main as mixer_main
from services.mixer.main import Playback
from services.mixer.mixing import BLOCK_SIZE, SAMPLE_RATE, mix_blocks, split_mp3_frames


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
    mixer.set_mic_live(True)
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
    mixer.play_file(str(wav), "music")
    mixer.set_mic_live(True)
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
    mixer.set_mic_live(True)
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


def _wav(tmp_path, name, seconds):
    path = tmp_path / name
    t = np.linspace(0, seconds * 440 * 2 * np.pi, int(seconds * SAMPLE_RATE))
    sf.write(path, 0.4 * np.sin(t), SAMPLE_RATE)
    return str(path)


def _peak_level(mixer, seconds):
    deadline = time.time() + seconds
    peak = 0.0
    while time.time() < deadline:
        peak = max(peak, mixer.level)
        time.sleep(0.01)
    return peak


def test_play_file_sets_bell_flag_and_finishes(tmp_path):
    mixer = Mixer(mic=FakeMic())
    mixer.start()
    try:
        playback = mixer.play_file(_wav(tmp_path, "b.wav", 0.25), "bell")
        packets = _collect(mixer.buffer, 14)
        assert playback.done.wait(3) and not playback.cancelled
    finally:
        mixer.stop()
    assert any(p.status & MixerStatus.BELL for p in packets)
    assert not mixer.file_playing


def test_new_play_file_cancels_previous(tmp_path):
    mixer = Mixer(mic=FakeMic())
    first = mixer.play_file(_wav(tmp_path, "a.wav", 2), "music")
    second = mixer.play_file(_wav(tmp_path, "b.wav", 1), "bell")
    assert first.cancelled and first.done.is_set()
    assert mixer.file_playing and not second.done.is_set()


def test_stop_file_only_stops_matching_playback(tmp_path):
    mixer = Mixer(mic=FakeMic())
    playback = mixer.play_file(_wav(tmp_path, "a.wav", 2), "music")
    mixer.stop_file(Playback("music", 1.0))  # nem az aktualis: nem tortenik semmi
    assert mixer.file_playing and not playback.cancelled
    mixer.stop_file(playback)
    assert playback.cancelled and not mixer.file_playing


def test_unreadable_file_raises_player_error(tmp_path):
    bad = tmp_path / "rossz.wav"
    bad.write_bytes(b"nem hang")
    with pytest.raises(AudioPlayerError):
        Mixer(mic=FakeMic()).play_file(str(bad), "music")


def test_unknown_kind_rejected(tmp_path):
    with pytest.raises(ValueError):
        Mixer(mic=FakeMic()).play_file(_wav(tmp_path, "a.wav", 0.1), "zaj")


def test_stop_wakes_waiting_playback(tmp_path):
    mixer = Mixer(mic=FakeMic())
    mixer.start()
    playback = mixer.play_file(_wav(tmp_path, "a.wav", 5), "music")
    mixer.stop()
    assert playback.done.wait(1) and playback.cancelled


def test_bell_ignores_music_volume(tmp_path):
    mixer = Mixer(mic=FakeMic())
    mixer.change_volume(0.0)  # a mikrofon nem zavar
    mixer.change_music_volume(0.0)
    mixer.start()
    try:
        mixer.play_file(_wav(tmp_path, "csengo.wav", 1), "bell")
        assert _peak_level(mixer, 0.5) > 0.1
        mixer.play_file(_wav(tmp_path, "zene.wav", 1), "music")
        _peak_level(mixer, 0.7)  # a csengo lecsengese (0.4 * 0.85^27 < 0.01)
        assert _peak_level(mixer, 0.3) < 0.01
    finally:
        mixer.stop()


def test_mic_is_off_by_default_and_not_mixed():
    mixer = Mixer(mic=FakeMic())
    assert mixer.mic_live is False
    mixer.start()
    try:
        packets = _collect(mixer.buffer, 6)
        assert not any(p.status & MixerStatus.PLAYING_MIC for p in packets)
        assert mixer.level < 0.01
    finally:
        mixer.stop()


def test_mic_live_toggle_opens_and_closes_stream():
    class SpyMic(FakeMic):
        def __init__(self):
            super().__init__()
            self.events = []

        def start(self, samplerate, blocksize):
            self.events.append("start")

        def stop(self):
            self.events.append("stop")

    mic = SpyMic()
    mixer = Mixer(mic=mic)
    mixer.set_mic_live(True)
    assert mixer.mic_live and mic.events == ["start"]
    mixer.set_mic_live(True)  # ismetelt bekapcsolas nem nyit ujra
    assert mic.events == ["start"]
    mixer.set_mic_live(False)
    assert not mixer.mic_live and mic.events == ["start", "stop"]


def test_mic_live_failure_keeps_it_off():
    class BrokenMic(FakeMic):
        def start(self, samplerate, blocksize):
            raise RuntimeError("nincs mikrofon")

    mixer = Mixer(mic=BrokenMic())
    with pytest.raises(RuntimeError):
        mixer.set_mic_live(True)
    assert mixer.mic_live is False


class _SpyMic(FakeMic):
    def __init__(self):
        super().__init__()
        self.events = []

    def start(self, samplerate, blocksize):
        self.events.append("start")

    def stop(self):
        self.events.append("stop")


def test_mic_off_during_slow_start_ends_off():
    class SlowMic(_SpyMic):
        def __init__(self):
            super().__init__()
            self.entered, self.release = threading.Event(), threading.Event()

        def start(self, samplerate, blocksize):
            super().start(samplerate, blocksize)
            self.entered.set()
            self.release.wait(5)

    mic = SlowMic()
    mixer = Mixer(mic=mic)
    on = threading.Thread(target=mixer.set_mic_live, args=(True,))
    on.start()
    assert mic.entered.wait(2)
    off = threading.Thread(target=mixer.set_mic_live, args=(False,))
    off.start()
    off.join(0.2)  # a kikapcsolas a bekapcsolas utan fut le
    mic.release.set()
    on.join(2)
    off.join(2)
    assert mixer.mic_live is False
    assert mic.events[-1] == "stop"


def test_output_device_change_with_mic_off_does_not_record():
    mic = _SpyMic()
    mixer = Mixer(mic=mic)
    mixer.change_output_device(InputDevice(index=3, name="masik"))
    assert mic.events == [] and mic.current_output_device.index == 3


def test_output_device_change_racing_mic_off_does_not_reopen():
    class SlowStopMic(_SpyMic):
        def __init__(self):
            super().__init__()
            self.stopping, self.release = threading.Event(), threading.Event()

        def stop(self):
            super().stop()
            self.stopping.set()
            self.release.wait(5)

    mic = SlowStopMic()
    mixer = Mixer(mic=mic)
    mixer.set_mic_live(True)
    off = threading.Thread(target=mixer.set_mic_live, args=(False,))
    off.start()
    assert mic.stopping.wait(2)
    change = threading.Thread(target=mixer.change_output_device, args=(InputDevice(index=3, name="masik"),))
    change.start()
    change.join(0.2)
    mic.release.set()
    off.join(2)
    change.join(2)
    assert mixer.mic_live is False
    assert "start" not in mic.events[mic.events.index("stop"):], mic.events


def test_failed_output_device_change_turns_mic_off():
    class FailSecondMic(_SpyMic):
        def start(self, samplerate, blocksize):
            super().start(samplerate, blocksize)
            if self.events.count("start") > 1:
                raise RuntimeError("az uj eszkoz nem nyithato")

    mic = FailSecondMic()
    mixer = Mixer(mic=mic)
    mixer.set_mic_live(True)
    with pytest.raises(RuntimeError):
        mixer.change_output_device(InputDevice(index=3, name="masik"))
    assert mixer.mic_live is False
    assert mic.events[-1] == "stop"


def test_mixer_thread_survives_unexpected_error(caplog):
    class FlakyMic(FakeMic):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def read_block(self):
            self.calls += 1
            if self.calls == 3:
                raise RuntimeError("varatlan mikrofonhiba")
            return super().read_block()

    mixer = Mixer(mic=FlakyMic())
    mixer.set_mic_live(True)
    with caplog.at_level(logging.ERROR, logger="csengetes"):
        mixer.start()
        try:
            packets = _collect(mixer.buffer, 15)
            assert mixer.running
        finally:
            mixer.stop()
    assert len(packets) == 15
    assert any(r.exc_info and "varatlan mikrofonhiba" in str(r.exc_info[1]) for r in caplog.records)


def test_mic_live_mixes_microphone():
    mixer = Mixer(mic=FakeMic())
    mixer.set_mic_live(True)
    mixer.start()
    try:
        packets = _collect(mixer.buffer, 6)
        assert any(p.status & MixerStatus.PLAYING_MIC for p in packets)
        assert wait_until(lambda: mixer.level > 0.2)
    finally:
        mixer.stop()
    assert mixer.mic_live is False


def test_local_output_receives_the_mixed_blocks():
    sink = FakeSink()
    calls = []

    def factory(index):
        calls.append(index)
        return sink

    mixer = Mixer(mic=FakeMic(), output_factory=factory)
    mixer.set_mic_live(True)
    mixer.start()
    try:
        _collect(mixer.buffer, 6)
        assert wait_until(lambda: len(sink.blocks) >= 6)
    finally:
        mixer.stop()
    assert calls == [None] and sink.closed
    assert sink.blocks[0].dtype == np.int16 and len(sink.blocks[0]) == BLOCK_SIZE
    assert mixer.output_ok is True


def test_output_device_change_reopens_sink():
    sinks, calls = [], []

    def factory(index):
        calls.append(index)
        sinks.append(FakeSink())
        return sinks[-1]

    mixer = Mixer(mic=FakeMic(), output_factory=factory)
    mixer.start()
    try:
        assert wait_until(lambda: len(sinks) == 1 and len(sinks[0].blocks) > 0)
        mixer.set_output_device(5)
        assert wait_until(lambda: calls == [None, 5])
        assert sinks[0].closed
    finally:
        mixer.stop()
    assert mixer.output_device_index == 5


def test_unopenable_output_keeps_the_stream_running():
    def factory(index):
        raise OSError("nincs eszkoz")

    mixer = Mixer(mic=FakeMic(), output_factory=factory)
    mixer.set_mic_live(True)
    mixer.start()
    try:
        packets = _collect(mixer.buffer, 6)
        assert mixer.output_ok is False
    finally:
        mixer.stop()
    assert len(packets) == 6


def test_output_lost_midway_keeps_the_stream_running():
    sink = FakeSink(fail_after=3)
    mixer = Mixer(mic=FakeMic(), output_factory=lambda index: sink)
    mixer.start()
    try:
        assert wait_until(lambda: mixer.output_ok is False)
        packets = _collect(mixer.buffer, 20)
    finally:
        mixer.stop()
    assert sink.closed and len(packets) == 20


# ---- F5: darabolt dekodolas ----

def _reference_load(path):
    """A korabbi (egyben dekodolo, np.interp-es) load_music: osszehasonlitasi alap."""
    data, rate = sf.read(path, dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    if rate != SAMPLE_RATE:
        target = int(len(mono) * SAMPLE_RATE / rate)
        mono = np.interp(np.linspace(0, len(mono) - 1, target), np.arange(len(mono)), mono).astype(np.float32)
    return mono


def _stereo_48k(tmp_path, seconds, name="sztereo.wav"):
    path = tmp_path / name
    t = np.arange(int(seconds * 48000)) / 48000
    left = 0.4 * np.sin(2 * np.pi * 440 * t)
    right = 0.3 * np.sin(2 * np.pi * 660 * t)
    sf.write(path, np.column_stack([left, right]), 48000)
    return str(path)


def test_load_music_chunked_matches_reference(tmp_path):
    path = _stereo_48k(tmp_path, 3.3)  # tobb dekodolasi darabon at
    got = mixer_main.load_music(path)
    ref = _reference_load(path)
    assert got.dtype == np.float32 and got.ndim == 1
    assert abs(len(got) - len(ref)) <= 1
    n = min(len(got), len(ref))
    assert np.max(np.abs(got[:n] - ref[:n])) < 1e-3


def test_load_music_same_rate_mono_passthrough(tmp_path):
    path = _wav(tmp_path, "a.wav", 1.7)
    got = mixer_main.load_music(path)
    assert got.dtype == np.float32
    np.testing.assert_allclose(got, _reference_load(path), atol=1e-6)


def test_load_music_peak_memory_is_bounded(tmp_path):
    import tracemalloc

    path = _stereo_48k(tmp_path, 10.0)
    tracemalloc.start()
    try:
        result = mixer_main.load_music(path)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak <= 4 * result.nbytes, f"csucs {peak} bajt, eredmeny {result.nbytes} bajt"


def test_too_long_file_rejected(tmp_path, monkeypatch):
    from types import SimpleNamespace

    path = _wav(tmp_path, "a.wav", 0.1)
    seconds = mixer_main.MAX_FILE_SECONDS + 1
    monkeypatch.setattr(mixer_main.sf, "info", lambda p: SimpleNamespace(
        frames=seconds * 44100, samplerate=44100, duration=float(seconds), channels=1))
    with pytest.raises(AudioPlayerError, match="tul hosszu"):
        mixer_main.load_music(path)
    with pytest.raises(AudioPlayerError, match="tul hosszu"):
        Mixer(mic=FakeMic()).decode_file(path)
