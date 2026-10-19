import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server
from fastapi.testclient import TestClient


def test_index_without_token_is_rejected():
    client = TestClient(server.app)
    resp = client.get("/")
    assert resp.status_code == 403


def test_index_with_wrong_token_is_rejected():
    client = TestClient(server.app)
    resp = client.get("/?token=wrong")
    assert resp.status_code == 403


def test_static_asset_without_session_is_rejected():
    client = TestClient(server.app)
    resp = client.get("/app.js")
    assert resp.status_code == 403


def test_api_without_session_is_rejected():
    client = TestClient(server.app)
    resp = client.get("/api/schedules")
    assert resp.status_code == 403


def test_index_with_correct_token_grants_session_for_later_requests():
    client = TestClient(server.app)
    resp = client.get(f"/?token={server.AUTH_TOKEN}")
    assert resp.status_code == 200

    # a kovetkezo kerelmek mar a munkamenet-sutit hasznaljak, token nelkul
    api_resp = client.get("/api/schedules")
    assert api_resp.status_code == 200

    asset_resp = client.get("/app.js")
    assert asset_resp.status_code == 200


def test_devices_api_requires_session():
    client = TestClient(server.app)
    assert client.get("/api/devices").status_code == 403
    assert client.post("/api/devices", json={"name": "A", "ip_address": "10.0.0.5"}).status_code == 403


def test_devices_api_available_with_session(tmp_path, monkeypatch):
    monkeypatch.setattr(server.devices, "_path", str(tmp_path / "devices.json"))
    monkeypatch.setattr(server.devices, "_devices", [])
    client = TestClient(server.app)
    client.get(f"/?token={server.AUTH_TOKEN}")
    r = client.post("/api/devices", json={"name": "A", "ip_address": "10.0.0.5"})
    assert r.status_code == 200 and r.json()["token"]
    assert client.get("/api/devices").json()["devices"][0]["name"] == "A"


def test_device_server_is_not_started_by_import():
    assert server.device_server._thread is None
    assert server.DEVICE_HOST == "0.0.0.0"
    assert server.HOST == "127.0.0.1"
