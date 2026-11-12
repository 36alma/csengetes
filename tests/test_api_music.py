import io
import os
import sys
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import music as api_music
from api import storage as api_storage
from services.music import MusicService, MusicSession
from services.music import download

ENVS = (
    "CSENGETES_MAX_STORAGE_MB",
    "CSENGETES_MAX_UPLOAD_MB",
    "CSENGETES_MAX_DOWNLOAD_MB",
    "CSENGETES_MAX_YT_MINUTES",
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(download, "DOWNLOAD_DIR", str(tmp_path / "download"))
    for e in ENVS:
        monkeypatch.delenv(e, raising=False)
    ctx = SimpleNamespace(media_dir=str(tmp_path / "media"), music_session=MusicSession(),
                          music_player=None, time_src=None)
    app = FastAPI()
    app.include_router(api_music.create_router(ctx))
    app.include_router(api_storage.create_router(ctx))
    return SimpleNamespace(client=TestClient(app), music_dir=tmp_path / "download", ctx=ctx)


def _wav_bytes(seconds=1.0):
    buf = io.BytesIO()
    sf.write(buf, np.zeros(int(44100 * seconds), "float32"), 44100, format="WAV")
    return buf.getvalue()


def test_upload_saves_file_and_lists_it(env):
    resp = env.client.post("/api/music/upload", files={"file": ("tone.wav", _wav_bytes(), "audio/wav")})

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "filename": "tone.wav"}
    assert (env.music_dir / "tone.wav").is_file()
    data = env.client.get("/api/music").json()
    assert data["files"] == ["tone.wav"]
    assert data["items"][0]["name"] == "tone.wav"
    assert abs(data["items"][0]["duration_s"] - 1.0) < 0.05


def test_upload_rejects_bad_extension(env):
    resp = env.client.post("/api/music/upload", files={"file": ("notes.txt", b"szoveg", "text/plain")})

    assert resp.status_code == 400
    assert "error" in resp.json()
    assert not (env.music_dir / "notes.txt").exists()


def test_upload_rejects_empty_secure_name(env):
    # A "..." nev secure_filename utan ures string marad.
    resp = env.client.post("/api/music/upload", files={"file": ("...", b"x" * 10, "audio/mpeg")})

    assert resp.status_code == 400
    assert "error" in resp.json()
    assert not env.music_dir.exists() or list(env.music_dir.iterdir()) == []


def test_upload_name_collision_is_409(env):
    env.music_dir.mkdir(parents=True)
    (env.music_dir / "bell.mp3").write_bytes(b"eredeti")

    resp = env.client.post("/api/music/upload", files={"file": ("bell.mp3", b"uj tartalom", "audio/mpeg")})

    assert resp.status_code == 409
    assert resp.json() == {"error": "Mar letezik ilyen nevu fajl."}
    assert (env.music_dir / "bell.mp3").read_bytes() == b"eredeti"


def test_upload_over_limit_is_413(env, monkeypatch):
    monkeypatch.setenv("CSENGETES_MAX_UPLOAD_MB", "0.001")

    resp = env.client.post("/api/music/upload", files={"file": ("big.mp3", b"x" * 5000, "audio/mpeg")})

    assert resp.status_code == 413
    assert "tul nagy" in resp.json()["error"]
    assert not (env.music_dir / "big.mp3").exists()


def test_upload_storage_full_is_507(env, monkeypatch):
    monkeypatch.setenv("CSENGETES_MAX_STORAGE_MB", "0.001")
    env.music_dir.mkdir(parents=True)
    (env.music_dir / "old.mp3").write_bytes(b"x" * 2000)

    resp = env.client.post("/api/music/upload", files={"file": ("new.mp3", b"x" * 100, "audio/mpeg")})

    assert resp.status_code == 507
    assert "tarhely" in resp.json()["error"]
    assert not (env.music_dir / "new.mp3").exists()


def test_storage_endpoint(env, monkeypatch):
    monkeypatch.setenv("CSENGETES_MAX_STORAGE_MB", "10")
    monkeypatch.setenv("CSENGETES_MAX_UPLOAD_MB", "3")
    monkeypatch.setenv("CSENGETES_MAX_DOWNLOAD_MB", "50")
    monkeypatch.setenv("CSENGETES_MAX_YT_MINUTES", "7")
    env.client.post("/api/music/upload", files={"file": ("a.mp3", b"x" * 100, "audio/mpeg")})

    data = env.client.get("/api/storage").json()

    assert set(data) == {"used_bytes", "max_bytes", "max_upload_bytes", "max_download_bytes", "max_yt_minutes"}
    assert data["used_bytes"] == 100
    assert data["max_bytes"] == 10 * 1024 * 1024
    assert data["max_upload_bytes"] == 3 * 1024 * 1024
    assert data["max_download_bytes"] == 50 * 1024 * 1024
    assert data["max_yt_minutes"] == 7


def test_storage_endpoint_defaults(env):
    data = env.client.get("/api/storage").json()

    assert data["used_bytes"] == 0
    assert data["max_bytes"] == 5120 * 1024 * 1024
    assert data["max_download_bytes"] == 100 * 1024 * 1024
    assert data["max_yt_minutes"] == 15


def test_delete_blocks_traversal_and_missing(env):
    env.music_dir.mkdir(parents=True)
    (env.music_dir.parent / "x.mp3").write_bytes(b"kivul")

    resp = env.client.post("/api/music/play-file", json={"filenames": ["../x.mp3"]})
    assert resp.status_code == 404
    assert "error" in resp.json()
    assert (env.music_dir.parent / "x.mp3").exists()
    assert env.client.delete("/api/music/nincs.mp3").status_code == 404


def test_delete_while_playing_is_409_until_done(env):
    env.music_dir.mkdir(parents=True)
    path = env.music_dir / "szol.mp3"
    path.write_bytes(b"x")
    svc = SimpleNamespace(status="playing", filename=[os.path.realpath(str(path))])
    env.ctx.music_session._current = svc

    resp = env.client.delete("/api/music/szol.mp3")
    assert resp.status_code == 409
    assert "error" in resp.json()
    assert path.exists()

    svc.status = "done"
    assert env.client.delete("/api/music/szol.mp3").status_code == 200
    assert not path.exists()


def test_delete_removes_file(env):
    env.music_dir.mkdir(parents=True)
    (env.music_dir / "torlendo.mp3").write_bytes(b"x")

    resp = env.client.delete("/api/music/torlendo.mp3")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert not (env.music_dir / "torlendo.mp3").exists()


def test_non_music_extensions_are_not_listed_or_addressable(env):
    env.music_dir.mkdir(parents=True)
    (env.music_dir / "letoltes.part").write_bytes(b"felkesz")
    (env.music_dir / "jegyzet.txt").write_bytes(b"szoveg")

    assert env.client.get("/api/music").json()["files"] == []
    assert env.client.delete("/api/music/letoltes.part").status_code == 404
    assert env.client.delete("/api/music/jegyzet.TXT").status_code == 404
    assert env.client.post("/api/music/play-file", json={"filenames": ["jegyzet.txt"]}).status_code == 404
    assert (env.music_dir / "letoltes.part").exists()
    assert (env.music_dir / "jegyzet.txt").exists()


def test_play_rejects_too_long_video_with_400(env, monkeypatch):
    def too_long(self):
        raise api_music.MusicDownloadError("tul hosszu")

    monkeypatch.setattr(api_music.MusicDownload, "check_limits", too_long)

    resp = env.client.post("/api/music/play", json={"urls": ["https://youtu.be/abc"]})

    assert resp.status_code == 400
    assert resp.json() == {"error": "tul hosszu"}


def test_play_storage_full_is_507(env, monkeypatch):
    monkeypatch.setattr(
        api_music.MusicDownload,
        "check_limits",
        lambda self: [{"title": "Hosszu", "duration_s": 600.0, "est_bytes": 10**12}],
    )

    resp = env.client.post("/api/music/play", json={"urls": ["https://youtu.be/abc"]})

    assert resp.status_code == 507
    assert "tarhely" in resp.json()["error"]
    assert env.ctx.music_session.current is None


def test_play_busy_is_409_before_metadata_check(env, monkeypatch):
    calls = []
    monkeypatch.setattr(api_music.MusicDownload, "check_limits", lambda self: calls.append(self) or [])
    busy = MusicService(["https://youtu.be/elso"], None, None)
    busy.status = "downloading"
    env.ctx.music_session._current = busy

    resp = env.client.post("/api/music/play", json={"urls": ["https://youtu.be/masodik"]})

    assert resp.status_code == 409
    assert "error" in resp.json()
    assert calls == []


def test_play_non_youtube_url_is_400(env, monkeypatch):
    calls = []
    monkeypatch.setattr(api_music.MusicDownload, "check_limits", lambda self: calls.append(self) or [])

    resp = env.client.post("/api/music/play", json={"urls": ["https://example.com/video"]})

    assert resp.status_code == 400
    assert "error" in resp.json()
    assert calls == []
