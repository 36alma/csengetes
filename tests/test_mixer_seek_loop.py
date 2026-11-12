import os
import sys

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fakes import FakeMic, wait_until
from services.mixer import Mixer, MixerPlayer, PlayGate
from services.mixer import player as player_module
from services.mixer.mixing import BLOCK_SIZE, SAMPLE_RATE

SR, BS = SAMPLE_RATE, BLOCK_SIZE


@pytest.fixture
def mixer():
    return Mixer(mic=FakeMic())


def make(mixer, blocks=10):
    return mixer.play_samples(np.ones(BS * blocks, dtype=np.float32), "music")


def test_position_advances_per_block(mixer):
    make(mixer)
    for _ in range(3):
        mixer._next_file_block()
    assert mixer.position() == (3 * BS / SR, 10 * BS / SR)


def test_seek_clamps_and_returns_new_position(mixer):
    make(mixer)
    assert mixer.seek(-5) == 0.0
    assert mixer.seek(1e9) == 10 * BS / SR


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_seek_nonfinite_raises(mixer, bad):
    make(mixer)
    with pytest.raises(ValueError):
        mixer.seek(bad)
    assert mixer.position() == (0.0, 10 * BS / SR)  # az allapot nem valtozott


def test_seek_without_playback_or_wrong_kind_is_none(mixer):
    assert mixer.seek(1.0) is None
    make(mixer)
    assert mixer.seek(1.0, kind="bell") is None


def test_backward_seek_extends_extra_s(mixer):
    pb = make(mixer)
    for _ in range(5):
        mixer._next_file_block()
    mixer.seek(0)
    assert pb.extra_s == pytest.approx(5 * BS / SR)


def test_seek_to_end_finishes_playback(mixer):
    pb = make(mixer)
    mixer.seek(1e9)
    mixer._next_file_block()
    assert pb.done.is_set() and mixer.position() is None


def test_loop_wraps_instead_of_finishing(mixer):
    pb = make(mixer, 2)
    mixer.set_loop(True)
    for _ in range(5):
        mixer._next_file_block()
    assert not pb.done.is_set() and pb.extra_s > 0


def test_loop_off_finishes(mixer):
    pb = make(mixer, 2)
    for _ in range(3):
        mixer._next_file_block()
    assert pb.done.is_set()


def test_player_set_loop_and_seek_apply_to_own_kind(mixer, tmp_path):
    player = MixerPlayer(mixer, "music", allowed_roots=[str(tmp_path)], device_lister=lambda: [])
    pb = make(mixer)
    player.set_loop(True)
    assert pb.loop
    player.set_loop(False)
    assert not pb.loop
    assert player.seek(0.1) == pytest.approx(0.1)
    pos, length = player.position()
    assert pos == pytest.approx(0.1) and length == pytest.approx(10 * BS / SR)
    bell = MixerPlayer(mixer, "bell", allowed_roots=[str(tmp_path)], device_lister=lambda: [])
    assert bell.seek(0.1) is None and bell.position() is None


@pytest.fixture
def env(tmp_path):
    mixer = Mixer(mic=FakeMic())
    player = MixerPlayer(
        mixer,
        "music",
        gate=PlayGate(),
        allowed_roots=[str(tmp_path)],
        device_lister=lambda: [],
    )
    mixer.start()
    yield tmp_path, mixer, player
    mixer.stop()


def write_wav(tmp_path, name, seconds):
    path = tmp_path / name
    t = np.arange(int(seconds * SR)) / SR
    sf.write(path, 0.4 * np.sin(2 * np.pi * 440 * t), SR)
    return str(path)


def test_player_loop_longer_than_duration_does_not_trip_watchdog(env, monkeypatch):
    tmp_path, mixer, player = env
    monkeypatch.setattr(player_module, "WATCHDOG_MARGIN_S", 0.1)
    path = write_wav(tmp_path, "rovid.wav", 0.2)
    player.set_loop(True)
    done = []
    player.play(path, on_done=done.append)
    assert wait_until(player.is_playing)
    # 1 mp: a 0.2 mp-es fajl tobbszor korbeer; a watchdognak nem szabad megszakitania
    assert not wait_until(lambda: bool(done), timeout=1.0), done
    assert player.is_playing()
    player.stop()
    assert wait_until(lambda: done == [None])
