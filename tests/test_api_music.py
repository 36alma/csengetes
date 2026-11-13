import io
import json
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
from services.music import MusicService, MusicSession, PlaylistStore
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
                          music_player=None, time_src=None,
                          playlists=PlaylistStore(str(tmp_path / "pl.json")))
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


def test_delete_os_error_is_409_and_keeps_file_and_playlists(env, monkeypatch):
    env.music_dir.mkdir(parents=True)
    (env.music_dir / "nyitva.mp3").write_bytes(b"x")
    playlist = env.ctx.playlists.create("Lista")
    env.ctx.playlists.add_tracks(playlist["id"], ["nyitva.mp3"])

    def locked(path):
        raise PermissionError("masik folyamat hasznalja")

    monkeypatch.setattr(api_music.os, "remove", locked)

    resp = env.client.delete("/api/music/nyitva.mp3")

    assert resp.status_code == 409
    assert resp.json() == {"error": "A fajl nem torolheto (hasznalatban lehet)."}
    assert (env.music_dir / "nyitva.mp3").exists()
    assert env.ctx.playlists.get(playlist["id"])["tracks"] == ["nyitva.mp3"]


@pytest.mark.parametrize("exc", [OSError("lemez hiba"), ValueError("serult adat")])
def test_delete_playlist_cascade_failure_is_500_json(env, monkeypatch, exc):
    env.music_dir.mkdir(parents=True)
    (env.music_dir / "torolt.mp3").write_bytes(b"x")

    def broken(name):
        raise exc

    monkeypatch.setattr(env.ctx.playlists, "remove_track_everywhere", broken)

    resp = env.client.delete("/api/music/torolt.mp3")

    assert resp.status_code == 500
    assert resp.json() == {"error": "A fajl torolve, de a playlistek frissitese nem sikerult."}
    assert not (env.music_dir / "torolt.mp3").exists()


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


class _ClampPlayer:
    """Mixer helyettes: a tekeres a [0, hossz] tartomanyba szoritja, mint a valodi lejatszo."""

    def __init__(self, duration=10.0):
        self.duration = duration
        self.pos = 0.0

    def play(self, path, blocking=False, force=False, on_done=None):
        pass

    def position(self):
        return (self.pos, self.duration)

    def seek(self, seconds):
        self.pos = max(0.0, min(seconds, self.duration))
        return self.pos

    def stop(self):
        pass

    def set_loop(self, enabled):
        pass


def _playing(env):
    """Futo zene a sessionben (nincs szal): ket szam, az elso szol."""
    player = _ClampPlayer()
    names = [str(env.music_dir / "a.mp3"), str(env.music_dir / "b.mp3")]
    svc = MusicService(None, player, None, files=names, force=True)
    svc.status = "playing"
    svc.index = 0
    svc.current = names[0]
    env.ctx.music_session._current = svc
    return player


def test_seek_position_ok(env):
    player = _playing(env)

    resp = env.client.post("/api/music/seek", json={"position_s": 2.5})

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "position_s": 2.5}
    assert player.pos == 2.5


def test_seek_delta_ok(env):
    player = _playing(env)
    player.pos = 2.0

    resp = env.client.post("/api/music/seek", json={"delta_s": 3.0})

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "position_s": 5.0}
    assert player.pos == 5.0


def test_seek_nothing_playing_is_409(env):
    resp = env.client.post("/api/music/seek", json={"position_s": 1})

    assert resp.status_code == 409
    assert resp.json() == {"error": "Nem szol zene."}


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"position_s": "x"},
        {"position_s": True},
        {"position_s": float("nan")},
        {"position_s": float("inf")},
        {"position_s": None},
        {"position_s": [1]},
        {"position_s": 10**400},
        {"delta_s": 1e10},
        {"position_s": 1, "delta_s": 1},
        [1, 2],
        None,
    ],
)
def test_seek_rejects_garbage(env, body):
    _playing(env)

    # json=... nem kuldi a NaN/inf-et; a nyers szoveg a valodi kliens-esetet adja
    resp = env.client.post("/api/music/seek", content=json.dumps(body),
                           headers={"Content-Type": "application/json"})

    assert resp.status_code == 400
    assert "error" in resp.json()


def test_seek_past_end_is_clamped_not_500(env):
    player = _playing(env)

    resp = env.client.post("/api/music/seek", json={"position_s": 1e6})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "position_s": 10.0}

    resp = env.client.post("/api/music/seek", json={"delta_s": -1e6})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "position_s": 0.0}
    assert player.pos == 0.0


def test_loop_sets_mode_even_when_idle(env):
    resp = env.client.post("/api/music/loop", json={"mode": "track"})

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "mode": "track"}
    assert env.ctx.music_session.loop_mode == "track"
    assert env.client.get("/api/music/status").json()["loop"] == "track"


@pytest.mark.parametrize("body", [{"mode": "bogus"}, {"mode": None}, {"mode": 1}, {"mode": ["track"]}, {}, None])
def test_loop_rejects_unknown_mode(env, body):
    resp = env.client.post("/api/music/loop", json=body)

    assert resp.status_code == 400
    assert "error" in resp.json()
    assert env.ctx.music_session.loop_mode == "off"


def test_library_play_inherits_the_session_loop_mode(env):
    env.music_dir.mkdir(parents=True)
    (env.music_dir / "a.mp3").write_bytes(b"x")
    env.ctx.music_player = _ClampPlayer()
    env.client.post("/api/music/loop", json={"mode": "track"})

    resp = env.client.post("/api/music/play-file", json={"filenames": ["a.mp3"], "force": True})

    assert resp.status_code == 202
    assert env.ctx.music_session.current.loop_mode == "track"
    assert env.client.get("/api/music/status").json()["loop"] == "track"


def test_status_has_queue_next_position(env):
    _playing(env)

    data = env.client.get("/api/music/status").json()

    assert set(data) == {"status", "files", "queue", "index", "current", "next", "loop",
                         "position_s", "duration_s", "progress", "error"}
    assert data["status"] == "playing"
    assert data["files"] == data["queue"] == ["a.mp3", "b.mp3"]
    assert data["index"] == 0
    assert data["current"] == "a.mp3"
    assert data["next"] == "b.mp3"
    assert data["loop"] == "off"
    assert data["position_s"] == 0.0
    assert data["duration_s"] == 10.0
    assert data["progress"] is None
    assert data["error"] is None
