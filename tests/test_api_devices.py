import os
import sys
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import devices as api_devices
from services.devices import DeviceStore, Presence


class Clock:
    now = 1_000_000

    def __call__(self):
        return self.now


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    store = DeviceStore(path=str(tmp_path / "devices.json"), token_days=90, clock=clock)
    presence = Presence()
    ctx = SimpleNamespace(devices=store, presence=presence)
    app = FastAPI()
    app.include_router(api_devices.create_router(ctx))
    return SimpleNamespace(client=TestClient(app), store=store, presence=presence, clock=clock)


def _create(env, name="Aula", ip="192.168.1.50", **extra):
    return env.client.post("/api/devices", json={"name": name, "ip_address": ip, **extra})


def test_create_returns_token_once_and_list_hides_it(env):
    r = _create(env, hostname="esp-aula")
    assert r.status_code == 200
    body = r.json()
    token = body["token"]
    assert token and body["device"]["name"] == "Aula" and body["device"]["hostname"] == "esp-aula"
    assert "token_hash" not in body["device"]
    listing = env.client.get("/api/devices")
    assert listing.status_code == 200
    text = listing.text
    assert token not in text and "token_hash" not in text
    assert [d["name"] for d in listing.json()["devices"]] == ["Aula"]


@pytest.mark.parametrize(
    "body, status",
    [
        ({}, 400),
        ({"name": "A"}, 400),
        ({"ip_address": "192.168.1.50"}, 400),
        ({"name": "A", "ip_address": "nem-ip"}, 400),
        ({"name": "a\nb", "ip_address": "192.168.1.50"}, 400),
        ({"name": 5, "ip_address": "192.168.1.50"}, 400),
    ],
)
def test_create_validation(env, body, status):
    r = env.client.post("/api/devices", json=body)
    assert r.status_code == status and "error" in r.json()
    assert env.store.list() == []


def test_create_with_non_object_body_is_400(env):
    assert env.client.post("/api/devices", json=[1, 2]).status_code == 400


def test_duplicate_ip_is_409(env):
    _create(env)
    r = _create(env, name="Masik")
    assert r.status_code == 409 and "error" in r.json()


def test_status_values_and_expiring_soon(env):
    d = _create(env).json()["device"]
    status = lambda: env.client.get("/api/devices").json()["devices"][0]
    assert status()["status"] == "offline" and status()["connected"] is False and status()["expiring_soon"] is False
    env.presence.enter(d["id"])
    assert status()["status"] == "connected" and status()["connected"] is True
    env.presence.leave(d["id"])
    env.clock.now += 80 * 86400
    assert status()["expiring_soon"] is True
    env.clock.now = d["token_expires_at"]
    assert status()["status"] == "expired"
    env.clock.now = 1_000_000
    env.client.patch(f"/api/devices/{d['id']}", json={"enabled": False})
    assert status()["status"] == "disabled"


def test_patch(env):
    a = _create(env).json()["device"]
    _create(env, name="B", ip="192.168.1.51")
    r = env.client.patch(f"/api/devices/{a['id']}", json={"name": "Aula 2", "ip_address": "192.168.1.60"})
    assert r.status_code == 200 and r.json()["device"]["name"] == "Aula 2"
    assert env.client.patch(f"/api/devices/{a['id']}", json={"ip_address": "192.168.1.51"}).status_code == 409
    assert env.client.patch(f"/api/devices/{a['id']}", json={"enabled": "igen"}).status_code == 400
    assert env.client.patch(f"/api/devices/{a['id']}", json={"token_hash": "x"}).status_code == 400
    assert env.client.patch("/api/devices/nincs", json={"name": "x"}).status_code == 404
    assert env.client.patch(f"/api/devices/{a['id']}", json=[]).status_code == 400


def test_rotate(env):
    created = _create(env).json()
    r = env.client.post(f"/api/devices/{created['device']['id']}/rotate")
    assert r.status_code == 200
    new_token = r.json()["token"]
    assert new_token != created["token"]
    assert env.store.find_by_token(created["token"]) is None
    assert env.store.find_by_token(new_token) is not None
    assert env.client.post("/api/devices/nincs/rotate").status_code == 404


def test_delete(env):
    d = _create(env).json()["device"]
    assert env.client.delete(f"/api/devices/{d['id']}").json() == {"ok": True}
    assert env.client.delete(f"/api/devices/{d['id']}").status_code == 404
    assert env.client.get("/api/devices").json()["devices"] == []
