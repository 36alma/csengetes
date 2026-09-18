import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server


def _authed_client():
    client = server.app.test_client()
    client.get(f"/?token={server.AUTH_TOKEN}")
    return client


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
