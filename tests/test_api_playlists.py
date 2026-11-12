import os
import sys
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import music as api_music
from api import playlists as api_playlists
from services.music import MusicSession, PlaylistStore
from services.music import download


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(download, "DOWNLOAD_DIR", str(tmp_path / "download"))
    ctx = SimpleNamespace(media_dir=str(tmp_path / "media"), music_session=MusicSession(),
                          music_player=None, time_src=None,
                          playlists=PlaylistStore(str(tmp_path / "pl.json")))
    app = FastAPI()
    app.include_router(api_playlists.create_router(ctx))
    app.include_router(api_music.create_router(ctx))
    music_dir = tmp_path / "download"
    music_dir.mkdir(parents=True)
    return SimpleNamespace(client=TestClient(app), music_dir=music_dir, ctx=ctx)


@pytest.fixture
def started(env, monkeypatch):
    """A session.start helyett: a kapott szolgaltatast rogziti, szal nelkul."""
    captured = []

    def fake_start(service):
        captured.append(service)
        return True

    monkeypatch.setattr(env.ctx.music_session, "start", fake_start)
    return captured


def _touch(env, *names):
    for name in names:
        (env.music_dir / name).write_bytes(b"x")


def _create(env, name):
    resp = env.client.post("/api/playlists", json={"name": name})
    assert resp.status_code == 200
    return resp.json()["playlist"]


def _basenames(service):
    return [os.path.basename(p) for p in service.filename]


def test_playlist_crud_roundtrip(env):
    created = env.client.post("/api/playlists", json={"name": "Reggeli"})
    assert created.status_code == 200
    pl = created.json()["playlist"]
    assert pl["name"] == "Reggeli" and pl["tracks"] == []

    listing = env.client.get("/api/playlists").json()
    assert listing == {"active": pl["id"], "playlists": [pl]}

    renamed = env.client.put(f"/api/playlists/{pl['id']}", json={"name": "Delutan"})
    assert renamed.status_code == 200
    assert renamed.json() == {"ok": True, "playlist": {"id": pl["id"], "name": "Delutan", "tracks": []}}

    deleted = env.client.delete(f"/api/playlists/{pl['id']}")
    assert deleted.status_code == 200
    assert deleted.json() == {"ok": True}
    assert env.client.get("/api/playlists").json() == {"active": None, "playlists": []}


def test_create_duplicate_name_is_400(env):
    _create(env, "Reggeli")

    resp = env.client.post("/api/playlists", json={"name": "reggeli"})

    assert resp.status_code == 400
    assert "error" in resp.json()


def test_add_tracks_unknown_file_is_404(env):
    _touch(env, "a.mp3")
    pl = _create(env, "Lista")

    resp = env.client.post(f"/api/playlists/{pl['id']}/tracks", json={"names": ["a.mp3", "nincs.mp3"]})

    assert resp.status_code == 404
    assert resp.json() == {"error": "A fajl nem talalhato."}
    assert env.client.get("/api/playlists").json()["playlists"][0]["tracks"] == []


def test_add_tracks_skips_duplicates_and_stores_basenames(env):
    _touch(env, "a.mp3", "b.wav")
    pl = _create(env, "Lista")

    resp = env.client.post(f"/api/playlists/{pl['id']}/tracks", json={"names": ["a.mp3", "../a.mp3"]})
    assert resp.status_code == 200
    assert resp.json()["playlist"]["tracks"] == ["a.mp3"]

    resp = env.client.post(f"/api/playlists/{pl['id']}/tracks", json={"names": ["a.mp3", "b.wav"]})
    assert resp.json()["playlist"]["tracks"] == ["a.mp3", "b.wav"]


@pytest.mark.parametrize("body", [{}, {"names": []}, {"names": "a.mp3"}, {"names": [1]}, None])
def test_add_tracks_rejects_bad_body(env, body):
    pl = _create(env, "Lista")

    resp = env.client.post(f"/api/playlists/{pl['id']}/tracks", json=body)

    assert resp.status_code == 400
    assert "error" in resp.json()


def test_move_and_remove_track(env):
    _touch(env, "a.mp3", "b.mp3", "c.mp3")
    pl = _create(env, "Lista")
    env.client.post(f"/api/playlists/{pl['id']}/tracks", json={"names": ["a.mp3", "b.mp3", "c.mp3"]})
    url = f"/api/playlists/{pl['id']}"

    moved = env.client.post(f"{url}/move", json={"from_index": 0, "to_index": 2})
    assert moved.status_code == 200
    assert moved.json()["playlist"]["tracks"] == ["b.mp3", "c.mp3", "a.mp3"]

    for bad in ({"from_index": 0, "to_index": 9}, {"from_index": True, "to_index": 0}, {"from_index": "1", "to_index": 0}, {}):
        assert env.client.post(f"{url}/move", json=bad).status_code == 400

    removed = env.client.delete(f"{url}/tracks/b.mp3")
    assert removed.status_code == 200
    assert removed.json()["playlist"]["tracks"] == ["c.mp3", "a.mp3"]

    noop = env.client.delete(f"{url}/tracks/nincs.mp3")
    assert noop.status_code == 200
    assert noop.json()["playlist"]["tracks"] == ["c.mp3", "a.mp3"]


def test_unknown_playlist_is_404(env):
    _touch(env, "a.mp3")
    missing = "/api/playlists/nincs"
    not_found = {"error": "A playlist nem talalhato."}

    assert env.client.put(missing, json={"name": "X"}).json() == not_found
    assert env.client.delete(missing).json() == not_found
    assert env.client.post(f"{missing}/tracks", json={"names": ["a.mp3"]}).status_code == 404
    assert env.client.post(f"{missing}/move", json={"from_index": 0, "to_index": 0}).status_code == 404
    assert env.client.delete(f"{missing}/tracks/a.mp3").status_code == 404
    assert env.client.post("/api/playlists/active", json={"id": "nincs"}).status_code == 404


def test_active_playlist_roundtrip(env):
    first = _create(env, "Elso")
    second = _create(env, "Masodik")
    assert env.client.get("/api/playlists").json()["active"] == first["id"]

    assert env.client.post("/api/playlists/active", json={"id": second["id"]}).json() == {"ok": True}
    assert env.client.get("/api/playlists").json()["active"] == second["id"]

    assert env.client.post("/api/playlists/active", json={"id": None}).json() == {"ok": True}
    assert env.client.get("/api/playlists").json()["active"] is None


def test_music_items_list_playlists(env):
    _touch(env, "a.mp3", "b.mp3")
    pl = _create(env, "Reggeli")
    env.client.post(f"/api/playlists/{pl['id']}/tracks", json={"names": ["a.mp3"]})

    items = {i["name"]: i for i in env.client.get("/api/music").json()["items"]}

    assert items["a.mp3"]["playlists"] == ["Reggeli"]
    assert items["b.mp3"]["playlists"] == []


def test_delete_music_removes_it_from_playlists(env):
    _touch(env, "a.mp3", "b.mp3")
    pl = _create(env, "Lista")
    env.client.post(f"/api/playlists/{pl['id']}/tracks", json={"names": ["a.mp3", "b.mp3"]})

    assert env.client.delete("/api/music/a.mp3").status_code == 200

    assert not (env.music_dir / "a.mp3").exists()
    assert env.client.get("/api/playlists").json()["playlists"][0]["tracks"] == ["b.mp3"]


def test_delete_playing_file_is_409(env):
    _touch(env, "szol.mp3")
    pl = _create(env, "Lista")
    env.client.post(f"/api/playlists/{pl['id']}/tracks", json={"names": ["szol.mp3"]})
    path = env.music_dir / "szol.mp3"
    svc = SimpleNamespace(status="playing", filename=[os.path.realpath(str(path))])
    env.ctx.music_session._current = svc

    resp = env.client.delete("/api/music/szol.mp3")

    assert resp.status_code == 409
    assert path.exists()
    assert env.client.get("/api/playlists").json()["playlists"][0]["tracks"] == ["szol.mp3"]


def test_play_playlist_starts_queue_in_order(env, started):
    _touch(env, "a.mp3", "b.mp3")
    pl = _create(env, "Lista")
    env.client.post(f"/api/playlists/{pl['id']}/tracks", json={"names": ["b.mp3", "a.mp3"]})

    resp = env.client.post("/api/music/play-playlist", json={"id": pl["id"], "force": True})

    assert resp.status_code == 202
    assert len(started) == 1
    assert _basenames(started[0]) == ["b.mp3", "a.mp3"]
    assert started[0].force is True


def test_play_playlist_empty_is_400(env, started):
    pl = _create(env, "Ures")

    resp = env.client.post("/api/music/play-playlist", json={"id": pl["id"]})

    assert resp.status_code == 400
    assert resp.json() == {"error": "A playlist ures vagy a fajlok hianyoznak."}
    assert started == []


def test_play_playlist_skips_missing_files_and_all_missing_is_400(env, started):
    _touch(env, "a.mp3", "b.mp3")
    pl = _create(env, "Lista")
    env.client.post(f"/api/playlists/{pl['id']}/tracks", json={"names": ["a.mp3", "b.mp3"]})
    (env.music_dir / "a.mp3").unlink()

    resp = env.client.post("/api/music/play-playlist", json={"id": pl["id"]})
    assert resp.status_code == 202
    assert _basenames(started[0]) == ["b.mp3"]

    (env.music_dir / "b.mp3").unlink()
    resp = env.client.post("/api/music/play-playlist", json={"id": pl["id"]})
    assert resp.status_code == 400
    assert resp.json() == {"error": "A playlist ures vagy a fajlok hianyoznak."}
    assert len(started) == 1


def test_play_playlist_without_id_uses_active(env, started):
    _touch(env, "a.mp3")
    pl = _create(env, "Aktiv")
    env.client.post(f"/api/playlists/{pl['id']}/tracks", json={"names": ["a.mp3"]})

    resp = env.client.post("/api/music/play-playlist", json={})

    assert resp.status_code == 202
    assert _basenames(started[0]) == ["a.mp3"]


def test_play_playlist_without_active_is_400(env, started):
    _create(env, "Elso")
    env.client.post("/api/playlists/active", json={"id": None})

    resp = env.client.post("/api/music/play-playlist", json={})

    assert resp.status_code == 400
    assert resp.json() == {"error": "Nincs aktiv playlist."}
    assert started == []


def test_play_playlist_unknown_id_is_404(env, started):
    resp = env.client.post("/api/music/play-playlist", json={"id": "nincs"})

    assert resp.status_code == 404
    assert resp.json() == {"error": "A playlist nem talalhato."}
    assert started == []


def test_play_playlist_busy_is_409(env, monkeypatch):
    _touch(env, "a.mp3")
    pl = _create(env, "Lista")
    env.client.post(f"/api/playlists/{pl['id']}/tracks", json={"names": ["a.mp3"]})
    monkeypatch.setattr(env.ctx.music_session, "start", lambda service: False)

    resp = env.client.post("/api/music/play-playlist", json={"id": pl["id"]})

    assert resp.status_code == 409
    assert "error" in resp.json()
