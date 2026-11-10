import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fakes import FakeMic, FakeSink, wait_until
from model import MixerStatus
from services.mixer import Mixer
from services.mixer.mixing import BLOCK_SIZE, mix_blocks


def _block(value):
    return np.full(BLOCK_SIZE, value, dtype=np.float32)


class Channel:
    """A keverőszál által látott felület: azonosító, hangerő, read_block."""

    def __init__(self, channel_id, value, volume=1.0):
        self.id = channel_id
        self.volume = volume
        self.block = None if value is None else _block(value)

    def read_block(self):
        return self.block


@pytest.fixture
def mixer():
    sink = FakeSink()
    instance = Mixer(mic=FakeMic(), output_factory=lambda index: sink)
    instance.sink = sink
    instance.start()
    yield instance
    instance.stop()


def test_mix_adds_each_extra_with_its_own_volume():
    out = mix_blocks(None, None, 1.0, 1.0, 1.0, extras=[(_block(0.25), 0.5), (_block(0.25), 1.0)])
    assert int(out[0]) == int(0.375 * 32767)


def test_mix_applies_master_to_extras_and_clips():
    out = mix_blocks(None, None, 1.0, 1.0, 2.0, extras=[(_block(0.25), 1.0)])
    assert int(out[0]) == int(0.5 * 32767)
    out = mix_blocks(None, None, 1.0, 1.0, 2.0, extras=[(_block(0.9), 1.0)])
    assert int(out[0]) == 32767


def test_mix_without_extras_is_unchanged():
    assert not mix_blocks(None, None, 1.0, 1.0, 1.0).any()


def test_remote_channel_reaches_the_output(mixer):
    mixer.remote_channels = (Channel("a", 0.25, volume=0.5),)
    expected = int(0.125 * 32767)
    assert wait_until(lambda: mixer.sink.blocks and abs(int(mixer.sink.blocks[-1][0]) - expected) <= 1)


def test_two_remote_channels_are_mixed_together(mixer):
    mixer.remote_channels = (Channel("a", 0.25), Channel("b", 0.125))
    expected = int(0.375 * 32767)
    assert wait_until(lambda: mixer.sink.blocks and abs(int(mixer.sink.blocks[-1][0]) - expected) <= 1)


def test_levels_are_per_channel_after_its_volume(mixer):
    mixer.remote_channels = (Channel("a", 0.5, volume=0.5), Channel("b", 0.5, volume=1.0))
    assert wait_until(lambda: mixer.remote_levels.get("a", 0) == pytest.approx(0.25, abs=0.01))
    assert mixer.remote_levels["b"] == pytest.approx(0.5, abs=0.01)


def test_audible_remote_sets_the_mic_flag(mixer):
    mixer.remote_channels = (Channel("a", 0.25),)
    assert wait_until(lambda: MixerStatus.PLAYING_MIC in mixer.status)


def test_muted_remote_does_not_set_the_mic_flag(mixer):
    mixer.remote_channels = (Channel("a", 0.25, volume=0.0),)
    assert wait_until(lambda: "a" in mixer.remote_levels)
    assert wait_until(lambda: len(mixer.sink.blocks) > 3)
    assert MixerStatus.PLAYING_MIC not in mixer.status


def test_silent_remote_does_not_set_the_mic_flag(mixer):
    mixer.remote_channels = (Channel("a", None),)
    assert wait_until(lambda: "a" in mixer.remote_levels)
    assert wait_until(lambda: len(mixer.sink.blocks) > 3)
    assert MixerStatus.PLAYING_MIC not in mixer.status
    assert mixer.remote_levels["a"] == 0.0


def test_removed_channel_leaves_the_levels(mixer):
    mixer.remote_channels = (Channel("a", 0.25),)
    assert wait_until(lambda: "a" in mixer.remote_levels)
    mixer.remote_channels = ()
    assert wait_until(lambda: "a" not in mixer.remote_levels)


def test_stop_clears_remote_levels():
    instance = Mixer(mic=FakeMic(), output_factory=lambda index: FakeSink())
    instance.start()
    instance.remote_channels = (Channel("a", 0.25),)
    assert wait_until(lambda: "a" in instance.remote_levels)
    instance.stop()
    assert instance.remote_levels == {}
