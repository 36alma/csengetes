import os
import sys
import threading
import time

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fakes import FakeMic, wait_until
from model import MixerStatus
from services.mixer import AudioPlayerError, Mixer, MixerPlayer
from services.mixer.mixing import SAMPLE_RATE
from services.mixer.player import MAX_VOLUME_PERCENT

DEVICES = [(2, "Hangszoro"), (7, "HDMI")]


class Env:
    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.mixer = Mixer(mic=FakeMic())
        gate = threading.Lock()
        kwargs = dict(gate=gate, allowed_roots=[str(tmp_path)], device_lister=lambda: DEVICES)
        self.bell = MixerPlayer(self.mixer, "bell", **kwargs)
        self.music = MixerPlayer(self.mixer, "music", **kwargs)

    def wav(self, name, seconds):
        path = self.tmp / name
        t = np.linspace(0, seconds * 440 * 2 * np.pi, int(seconds * SAMPLE_RATE))
        sf.write(path, 0.4 * np.sin(t), SAMPLE_RATE)
        return str(path)


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    e.mixer.start()
    yield e
    e.mixer.stop()


def test_default_volume_is_100_percent(env):
    assert env.bell.get_volume() == 100.0


def test_set_volume_clamps_and_updates_master(env):
    env.bell.set_volume(150)
    assert env.mixer.master_volume == 1.5 and env.music.get_volume() == 150.0
    env.bell.set_volume(999)
    assert env.bell.get_volume() == MAX_VOLUME_PERCENT
    env.bell.set_volume(-5)
    assert env.bell.get_volume() == 0.0


@pytest.mark.parametrize("bad", ["hang", None, float("nan")])
def test_set_volume_rejects_garbage(env, bad):
    with pytest.raises(AudioPlayerError):
        env.bell.set_volume(bad)


@pytest.mark.parametrize("bad", [-1, "1", 1.5, True])
def test_set_device_rejects_invalid_index(env, bad):
    with pytest.raises(AudioPlayerError):
        env.bell.set_device(bad)


def test_device_roundtrip_by_name(env):
    env.bell.set_device(7)
    assert env.mixer.output_device_index == 7
    assert env.bell.get_device() == 7 and env.bell.get_device_name() == "HDMI"
    assert env.bell.find_device_index("Hangszoro") == 2
    assert env.bell.find_device_index("nincs ilyen") is None
    env.bell.set_device(None)
    assert env.bell.get_device_name() is None


def test_play_blocking_returns_after_file_ends_and_calls_on_done(env):
    done = []
    t0 = time.time()
    env.bell.play(env.wav("b.wav", 0.4), blocking=True, on_done=done.append)
    assert 0.3 < time.time() - t0 < 2.5
    assert done == [None] and not env.bell.is_playing()


def test_bell_sets_bell_flag(env):
    env.bell.play(env.wav("b.wav", 0.6))
    assert wait_until(lambda: bool(env.mixer.status & MixerStatus.BELL))


def test_non_blocking_play_runs_in_background(env):
    t0 = time.time()
    env.bell.play(env.wav("b.wav", 1.0))
    assert time.time() - t0 < 0.5
    assert wait_until(env.bell.is_playing)


def test_error_raised_without_on_done(env):
    with pytest.raises(AudioPlayerError):
        env.bell.play(str(env.tmp / "nincs.mp3"), blocking=True)


def test_error_reported_via_on_done(env):
    errors = []
    env.bell.play(str(env.tmp / "nincs.mp3"), blocking=True, on_done=errors.append)
    assert len(errors) == 1 and errors[0]


def test_unreadable_file_reported(env):
    bad = env.tmp / "rossz.wav"
    bad.write_bytes(b"nem hang")
    errors = []
    env.bell.play(str(bad), blocking=True, on_done=errors.append)
    assert errors == ["A fajl nem olvashato."]


def test_play_when_mixer_not_running_fails_clearly(tmp_path):
    e = Env(tmp_path)  # a mixer nincs elinditva
    errors = []
    t0 = time.time()
    e.bell.play(e.wav("b.wav", 0.2), blocking=True, on_done=errors.append)
    assert errors == ["A mixer nem fut."] and time.time() - t0 < 1.0


def test_stop_wakes_blocked_playback(env):
    done = []
    env.music.play(env.wav("hosszu.wav", 5.0), on_done=done.append)
    assert wait_until(env.music.is_playing)
    env.music.stop()
    assert wait_until(lambda: done == [None])
    assert not env.music.is_playing()


def test_bell_force_interrupts_music_without_error_and_next_track_waits(env):
    music_done = []
    env.music.play(env.wav("hosszu.wav", 3.0), on_done=music_done.append)
    assert wait_until(env.music.is_playing)

    t0 = time.time()
    env.bell.play(env.wav("csengo.wav", 0.4), blocking=True, force=True)
    assert time.time() - t0 < 1.5
    assert wait_until(lambda: music_done == [None])  # megszakitas nem hiba

    # a kovetkezo (nem force) lejatszas megvarja az elozo veget
    env.bell.play(env.wav("masodik.wav", 0.5))
    assert wait_until(env.bell.is_playing)
    t1 = time.time()
    env.music.play(env.wav("kovetkezo.wav", 0.2), blocking=True)
    assert time.time() - t1 > 0.3
