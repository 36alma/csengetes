import os
import sys

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.music.library import list_tracks, resolve_track, track_duration


def _write_wav(path, seconds=1.0):
    sf.write(path, np.zeros(int(44100 * seconds), "float32"), 44100)


def test_track_duration_wav_is_one_second(tmp_path):
    path = str(tmp_path / "a.wav")
    _write_wav(path)
    d = track_duration(path)
    assert d is not None
    assert abs(d - 1.0) < 0.05


def test_track_duration_garbage_returns_none(tmp_path):
    path = tmp_path / "rossz.mp3"
    path.write_bytes(b"nem hang tartalom" * 10)
    assert track_duration(str(path)) is None


def test_list_tracks_filters_extensions_and_sorts(tmp_path):
    _write_wav(str(tmp_path / "b.wav"))
    (tmp_path / "A.mp3").write_bytes(b"hamis")
    (tmp_path / "x.txt").write_bytes(b"szoveg")
    tracks = list_tracks(str(tmp_path))
    assert [t["name"] for t in tracks] == ["A.mp3", "b.wav"]
    assert tracks[0]["duration_s"] is None
    assert tracks[1]["duration_s"] is not None
    assert tracks[0]["size_bytes"] == len(b"hamis")


def test_list_tracks_missing_dir_returns_empty(tmp_path):
    assert list_tracks(str(tmp_path / "nincs")) == []


def test_resolve_track_blocks_traversal(tmp_path):
    root = tmp_path / "music"
    root.mkdir()
    (tmp_path / "x.wav").write_bytes(b"kint")
    _write_wav(str(root / "a.wav"))

    assert resolve_track(str(root), "../x.wav") is None
    assert resolve_track(str(root), "nincs.wav") is None
    assert resolve_track(str(root), "a.wav") == os.path.realpath(str(root / "a.wav"))
