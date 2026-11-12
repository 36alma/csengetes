import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import limits

ENVS = ["CSENGETES_MAX_STORAGE_MB", "CSENGETES_MAX_UPLOAD_MB", "CSENGETES_MAX_DOWNLOAD_MB", "CSENGETES_MAX_YT_MINUTES"]

def test_defaults(monkeypatch):
    for e in ENVS: monkeypatch.delenv(e, raising=False)
    monkeypatch.setattr(limits.config, "MAX_UPLOAD_MB", 20.0)
    assert limits.max_storage_bytes() == 5120 * 1024 * 1024
    assert limits.max_upload_bytes() == 20 * 1024 * 1024
    assert limits.max_download_bytes() == 100 * 1024 * 1024
    assert limits.max_yt_seconds() == 900

def test_env_overrides(monkeypatch):
    monkeypatch.setenv("CSENGETES_MAX_STORAGE_MB", "1.5"); monkeypatch.setenv("CSENGETES_MAX_YT_MINUTES", "2")
    assert limits.max_storage_bytes() == int(1.5 * 1024 * 1024)
    assert limits.max_yt_seconds() == 120

@pytest.mark.parametrize("bad", ["", "abc", "0", "-3", "nan", "inf"])
def test_invalid_env_falls_back_to_default(monkeypatch, bad):
    monkeypatch.setenv("CSENGETES_MAX_YT_MINUTES", bad)
    assert limits.max_yt_seconds() == 900

def test_dir_size_counts_all_dirs_and_ignores_missing(tmp_path):
    (tmp_path / "a").mkdir(); (tmp_path / "a" / "x.mp3").write_bytes(b"1" * 10)
    assert limits.dir_size_bytes(str(tmp_path / "a"), str(tmp_path / "nincs")) == 10

def test_check_space_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv("CSENGETES_MAX_STORAGE_MB", "1")
    (tmp_path / "f").write_bytes(b"1" * 1000)
    assert limits.check_space(1024 * 1024 - 1000, str(tmp_path)) is None      # pont betelik: engedett
    assert "tarhely" in limits.check_space(1024 * 1024 - 999, str(tmp_path))

def test_copy_limited(tmp_path):
    dest = tmp_path / "o.bin"
    assert limits.copy_limited(io.BytesIO(b"x" * 100), str(dest), 50) is False and not dest.exists()
    assert limits.copy_limited(io.BytesIO(b"x" * 100), str(dest), 100) is True and dest.stat().st_size == 100
