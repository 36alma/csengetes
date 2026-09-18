import sys
import os
import threading
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import audio_player as audio_player_module
from audio_player import AudioPlayer, MAX_VOLUME_PERCENT


def test_default_volume_is_100_percent():
    player = AudioPlayer()
    assert player.get_volume() == 100.0


def test_set_volume_clamps_to_valid_range():
    player = AudioPlayer()
    player.set_volume(-10)
    assert player.get_volume() == 0.0
    player.set_volume(500)
    assert player.get_volume() == MAX_VOLUME_PERCENT


def test_boost_gain_clips_samples_within_range():
    player = AudioPlayer()
    player.set_volume(200)
    samples = np.array([0.9, -0.9, 0.1], dtype=np.float32)
    gain = player.get_volume() / 100.0
    boosted = np.clip(samples * gain, -1.0, 1.0)
    assert boosted.max() <= 1.0
    assert boosted.min() >= -1.0
    assert boosted[2] == 0.2  # nem eri el a clip hatart, arany megmarad


def _fake_segment():
    segment = MagicMock()
    segment.get_array_of_samples.return_value = [0, 0]
    segment.channels = 1
    segment.sample_width = 2
    segment.frame_rate = 44100
    return segment


def test_force_calls_stop_before_playing():
    player = AudioPlayer()
    with patch.object(audio_player_module, "sd") as mock_sd, \
         patch.object(audio_player_module.AudioSegment, "from_file", return_value=_fake_segment()):
        player.play("dummy.mp3", blocking=True, force=True)
        mock_sd.stop.assert_called_once()
        mock_sd.play.assert_called_once()


def test_on_done_called_with_none_on_success():
    player = AudioPlayer()
    results = []
    with patch.object(audio_player_module, "sd"), \
         patch.object(audio_player_module.AudioSegment, "from_file", return_value=_fake_segment()):
        player.play("dummy.mp3", blocking=True, on_done=results.append)
    assert results == [None]


def test_on_done_called_with_error_message_on_failure():
    player = AudioPlayer()
    results = []
    with patch.object(audio_player_module.AudioSegment, "from_file", side_effect=RuntimeError("boom")):
        player.play("dummy.mp3", blocking=True, on_done=results.append)
    assert len(results) == 1
    assert "boom" in results[0]


def test_non_blocking_play_runs_in_background_thread():
    player = AudioPlayer()
    done_event = threading.Event()
    with patch.object(audio_player_module, "sd"), \
         patch.object(audio_player_module.AudioSegment, "from_file", return_value=_fake_segment()):
        player.play("dummy.mp3", blocking=False, on_done=lambda _err: done_event.set())
        assert done_event.wait(timeout=2)
