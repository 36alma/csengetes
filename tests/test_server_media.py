import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import server
from fastapi.testclient import TestClient
from services.music import download as music_download


def _authed_client():
    client = TestClient(server.app)
    client.get(f"/?token={server.AUTH_TOKEN}")
    return client


@pytest.fixture
def upload_dirs(tmp_path, monkeypatch):
    media = tmp_path / "media"
    media.mkdir()
    downloads = tmp_path / "download"
    downloads.mkdir()
    monkeypatch.setattr(server.API_CONTEXT, "media_dir", str(media))
    monkeypatch.setattr(music_download, "DOWNLOAD_DIR", str(downloads))
    monkeypatch.delenv("CSENGETES_MAX_STORAGE_MB", raising=False)
    monkeypatch.delenv("CSENGETES_MAX_UPLOAD_MB", raising=False)
    return media


def test_play_rejects_path_traversal_outside_media_dir(tmp_path, monkeypatch):
    outside_file = tmp_path / "secret.mp3"
    outside_file.write_bytes(b"fake-audio")

    calls = []
    monkeypatch.setattr(server.player, "play", lambda *a, **k: calls.append((a, k)))

    client = _authed_client()
    traversal_name = os.path.relpath(str(outside_file), server.MEDIA_DIR)
    resp = client.post("/api/media/play", json={"filename": traversal_name})

    assert resp.status_code == 404
    assert calls == []


def test_upload_over_env_limit_gets_413(upload_dirs, monkeypatch):
    monkeypatch.setenv("CSENGETES_MAX_UPLOAD_MB", "0.001")
    client = _authed_client()

    resp = client.post("/api/media/upload", files={"file": ("bell.mp3", b"x" * 5000, "audio/mpeg")})

    assert resp.status_code == 413
    assert "tul nagy" in resp.json()["error"]
    assert not (upload_dirs / "bell.mp3").exists()


def test_upload_when_storage_full_gets_507(upload_dirs, monkeypatch):
    monkeypatch.setenv("CSENGETES_MAX_STORAGE_MB", "0.001")
    (upload_dirs / "old.mp3").write_bytes(b"x" * 2000)
    client = _authed_client()

    resp = client.post("/api/media/upload", files={"file": ("bell.mp3", b"x" * 100, "audio/mpeg")})

    assert resp.status_code == 507
    assert "tarhely" in resp.json()["error"]
    assert not (upload_dirs / "bell.mp3").exists()


def test_upload_still_works_within_limits(upload_dirs):
    client = _authed_client()

    resp = client.post("/api/media/upload", files={"file": ("bell.mp3", b"x" * 100, "audio/mpeg")})

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "filename": "bell.mp3"}
    assert (upload_dirs / "bell.mp3").read_bytes() == b"x" * 100
