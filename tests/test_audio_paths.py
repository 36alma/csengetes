import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.mixer.errors import AudioPlayerError
from services.mixer.paths import normalize_roots, validate_audio_path


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    good = root / "bell.mp3"
    good.write_bytes(b"x" * 10)
    return SimpleNamespace(tmp=tmp_path, root=root, good=good, roots=normalize_roots([str(root)]))


def test_valid_file_returns_real_path(env):
    assert validate_audio_path(str(env.good), env.roots) == os.path.realpath(env.good)


@pytest.mark.parametrize(
    "bad",
    ["http://x/a.mp3", "//server/share/a.mp3", "\\\\server\\share\\a.mp3", "", "a\x00.mp3", b"bytes.mp3", None],
)
def test_non_local_or_malformed_paths_rejected(env, bad):
    with pytest.raises(AudioPlayerError):
        validate_audio_path(bad, env.roots)


def test_path_traversal_outside_root_rejected(env):
    outside = env.tmp / "outside.mp3"
    outside.write_bytes(b"x" * 10)
    with pytest.raises(AudioPlayerError):
        validate_audio_path(str(env.root / ".." / "outside.mp3"), env.roots)


def test_sibling_dir_with_same_prefix_rejected(env):
    sibling = env.tmp / "media2"
    sibling.mkdir()
    (sibling / "bell.mp3").write_bytes(b"x" * 10)
    with pytest.raises(AudioPlayerError):
        validate_audio_path(str(sibling / "bell.mp3"), env.roots)


@pytest.mark.parametrize("name", ["a.txt", "a.exe", "a.m4a", "a.wma", "noext"])
def test_disallowed_extensions_rejected(env, name):
    path = env.root / name
    path.write_bytes(b"x" * 10)
    with pytest.raises(AudioPlayerError):
        validate_audio_path(str(path), env.roots)


@pytest.mark.parametrize("name", ["a.mp3", "a.wav", "a.ogg", "a.flac", "a.opus", "A.MP3"])
def test_allowed_extensions_accepted(env, name):
    path = env.root / name
    path.write_bytes(b"x" * 10)
    assert validate_audio_path(str(path), env.roots)


def test_oversized_and_empty_files_rejected(env):
    empty = env.root / "empty.mp3"
    empty.write_bytes(b"")
    big = env.root / "big.mp3"
    big.write_bytes(b"x" * 100)
    with pytest.raises(AudioPlayerError):
        validate_audio_path(str(empty), env.roots)
    with pytest.raises(AudioPlayerError):
        validate_audio_path(str(big), env.roots, max_bytes=50)


def test_missing_file_rejected(env):
    with pytest.raises(AudioPlayerError):
        validate_audio_path(str(env.root / "nincs.mp3"), env.roots)


def test_symlink_escaping_root_rejected(env):
    outside = env.tmp / "outside.mp3"
    outside.write_bytes(b"x" * 10)
    link = env.root / "link.mp3"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("szimbolikus link nem hozhato letre ezen a gepen")
    with pytest.raises(AudioPlayerError):
        validate_audio_path(str(link), env.roots)


def test_error_message_does_not_leak_absolute_path(env):
    outside = env.tmp / "outside.mp3"
    outside.write_bytes(b"x" * 10)
    with pytest.raises(AudioPlayerError) as exc:
        validate_audio_path(str(outside), env.roots)
    assert str(env.tmp) not in str(exc.value)
