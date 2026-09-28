import sys
import os
import threading
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audio_player as ap
from audio_player import AudioPlayer, AudioPlayerError, MAX_VOLUME_PERCENT


class FakePlayer:
    """vlc.MediaPlayer helyettes: a play() azonnal 'veget er' (vagy hibazik)."""

    def __init__(self, fail_event=False, hang=False):
        self.fail_event = fail_event
        self.hang = hang
        self.callbacks = {}
        self.volume = None
        self.device = None
        self.stopped = 0
        self.released = False

    def set_media(self, media):
        pass

    def event_manager(self):
        mgr = MagicMock()
        mgr.event_attach.side_effect = lambda ev, cb: self.callbacks.__setitem__(ev, cb)
        return mgr

    def audio_set_volume(self, v):
        self.volume = v

    def audio_output_device_set(self, module, dev):
        self.device = dev

    def play(self):
        if self.hang:
            return 0
        key = "err" if self.fail_event else "end"
        cb = self.callbacks[ap.vlc.EventType.MediaPlayerEncounteredError
                            if self.fail_event else ap.vlc.EventType.MediaPlayerEndReached]
        cb(None)
        return 0

    def stop(self):
        self.stopped += 1

    def release(self):
        self.released = True


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    good = root / "a.mp3"
    good.write_bytes(b"x" * 100)
    player = AudioPlayer(allowed_roots=[str(root)], max_file_bytes=1000)
    media = MagicMock()
    media.subitems.return_value = None
    media.get_duration.return_value = 1000
    instance = MagicMock()
    instance.media_new_path.return_value = media
    fake = FakePlayer()
    instance.media_player_new.return_value = fake
    player._instance = instance
    return player, str(good), root, fake, instance, media


def test_default_volume_is_100_percent():
    assert AudioPlayer().get_volume() == 100.0


def test_set_volume_clamps_to_valid_range():
    player = AudioPlayer()
    player.set_volume(-10)
    assert player.get_volume() == 0.0
    player.set_volume(500)
    assert player.get_volume() == MAX_VOLUME_PERCENT


def test_set_volume_rejects_garbage():
    player = AudioPlayer()
    for bad in ("abc", None, float("nan")):
        with pytest.raises(AudioPlayerError):
            player.set_volume(bad)


def test_set_device_rejects_invalid_index():
    player = AudioPlayer()
    for bad in (-1, "1", 1.5, True):
        with pytest.raises(AudioPlayerError):
            player.set_device(bad)
    player.set_device(2)
    assert player.get_device() == 2
    player.set_device(None)
    assert player.get_device() is None


def test_play_success_calls_on_done_with_none_and_applies_volume(env):
    player, good, _root, fake, *_ = env
    player.set_volume(150)
    results = []
    player.play(good, blocking=True, on_done=results.append)
    assert results == [None]
    assert fake.volume == 150
    assert fake.released


def test_media_created_from_local_path_with_no_video(env):
    player, good, _root, _fake, instance, media = env
    player.play(good, blocking=True, on_done=lambda e: None)
    instance.media_new_path.assert_called_once_with(os.path.realpath(good))
    media.add_option.assert_any_call(":no-video")


def test_vlc_error_event_reported(env):
    player, good, _root, _fake, instance, _media = env
    instance.media_player_new.return_value = FakePlayer(fail_event=True)
    results = []
    player.play(good, blocking=True, on_done=results.append)
    assert len(results) == 1 and results[0]


def test_error_raised_without_on_done(env):
    player, _good, root, *_ = env
    with pytest.raises(AudioPlayerError):
        player.play(str(root / "missing.mp3"), blocking=True)


def test_non_blocking_play_runs_in_background_thread(env):
    player, good, *_ = env
    done_event = threading.Event()
    player.play(good, blocking=False, on_done=lambda _err: done_event.set())
    assert done_event.wait(timeout=2)


def test_stop_wakes_blocked_playback(env):
    player, good, _root, _fake, instance, _media = env
    instance.media_player_new.return_value = FakePlayer(hang=True)
    results = []
    t = threading.Thread(
        target=lambda: player.play(good, blocking=True, on_done=results.append))
    t.start()
    for _ in range(100):
        if player.is_playing():
            break
        threading.Event().wait(0.02)
    player.stop()
    t.join(timeout=2)
    assert not t.is_alive()
    assert results == [None]


def test_force_interrupts_current_and_non_force_waits(env):
    player, good, _root, _fake, instance, _media = env
    first = FakePlayer(hang=True)
    second = FakePlayer()
    instance.media_player_new.side_effect = [first, second]
    r1, r2 = [], []
    t1 = threading.Thread(target=lambda: player.play(good, blocking=True, on_done=r1.append))
    t1.start()
    for _ in range(100):
        if player.is_playing():
            break
        threading.Event().wait(0.02)
    player.play(good, blocking=True, force=True, on_done=r2.append)
    t1.join(timeout=2)
    assert r1 == [None] and r2 == [None]
    assert first.stopped >= 1


# ---------- biztonsag ----------

@pytest.mark.parametrize("bad", [
    "http://evil.example/a.mp3",
    "https://evil.example/a.mp3",
    "file:///C:/Windows/win.ini",
    "\\\\server\\share\\a.mp3",
    "//server/share/a.mp3",
    "rtsp://x/a.mp3",
    "",
    "a\x00.mp3",
])
def test_non_local_or_malformed_paths_rejected(env, bad):
    player, *_ = env
    with pytest.raises(AudioPlayerError):
        player._validate_path(bad)


def test_path_traversal_outside_root_rejected(env):
    player, _good, root, *_ = env
    outside = root.parent / "secret.mp3"
    outside.write_bytes(b"x")
    with pytest.raises(AudioPlayerError):
        player._validate_path(str(root / ".." / "secret.mp3"))
    with pytest.raises(AudioPlayerError):
        player._validate_path(str(outside))


def test_sibling_dir_with_same_prefix_rejected(env):
    player, _good, root, *_ = env
    evil = root.parent / "media_evil"
    evil.mkdir()
    f = evil / "a.mp3"
    f.write_bytes(b"x")
    with pytest.raises(AudioPlayerError):
        player._validate_path(str(f))


@pytest.mark.parametrize("name", ["a.m3u", "a.pls", "a.xspf", "a.asx", "a.txt", "a.exe", "a"])
def test_disallowed_extensions_rejected(env, name):
    player, _good, root, *_ = env
    (root / name).write_bytes(b"x")
    with pytest.raises(AudioPlayerError):
        player._validate_path(str(root / name))


def test_oversized_and_empty_files_rejected(env):
    player, _good, root, *_ = env
    (root / "big.mp3").write_bytes(b"x" * 2000)
    (root / "empty.mp3").write_bytes(b"")
    for name in ("big.mp3", "empty.mp3"):
        with pytest.raises(AudioPlayerError):
            player._validate_path(str(root / name))


def test_symlink_escaping_root_rejected(env):
    player, _good, root, *_ = env
    target = root.parent / "outside.mp3"
    target.write_bytes(b"x")
    link = root / "link.mp3"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink nem hozhato letre")
    with pytest.raises(AudioPlayerError):
        player._validate_path(str(link))


def test_playlist_content_rejected(env):
    player, good, _root, _fake, _instance, media = env
    subitems = MagicMock()
    subitems.count.return_value = 2
    media.subitems.return_value = subitems
    results = []
    player.play(good, blocking=True, on_done=results.append)
    assert results and results[0]


def test_error_message_does_not_leak_absolute_path(env):
    player, _good, root, *_ = env
    with pytest.raises(AudioPlayerError) as exc:
        player.play(str(root / "missing.mp3"), blocking=True)
    assert str(root) not in str(exc.value)


def test_missing_libvlc_gives_clear_error(tmp_path):
    root = tmp_path / "m"
    root.mkdir()
    f = root / "a.mp3"
    f.write_bytes(b"x")
    player = AudioPlayer(allowed_roots=[str(root)])
    with patch.object(ap, "vlc", None):
        assert player.list_output_devices() == []
        with pytest.raises(AudioPlayerError):
            player.play(str(f), blocking=True)


def test_vlc_instance_uses_hardening_options():
    assert "--no-lua" in ap.VLC_OPTIONS
    assert "--no-video" in ap.VLC_OPTIONS
    assert "--no-metadata-network-access" in ap.VLC_OPTIONS
    assert "--no-sub-autodetect-file" in ap.VLC_OPTIONS


def test_device_name_roundtrip_survives_index_shift():
    player = AudioPlayer()
    with patch.object(player, "list_output_devices", return_value=[(0, "Default"), (1, "USB"), (2, "HDMI")]):
        player.set_device(2)
        assert player.get_device_name() == "HDMI"
    # az eszkozlista atrendezodott: a nev alapjan az uj index talalhato meg
    with patch.object(player, "list_output_devices", return_value=[(0, "Default"), (1, "HDMI")]):
        assert player.find_device_index("HDMI") == 1
        assert player.find_device_index("Eltunt") is None
        assert player.find_device_index(None) is None
