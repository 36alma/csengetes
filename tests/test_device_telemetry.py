"""Az ESP32 telemetria: buffer-allapot es naplo POST (tokennel), admin lekerdezes, korlatok."""

import json
import os
import sys
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fakes import FakeMic, wait_until
import device_server
from api import devices as api_devices
from services.devices import DeviceStore, DeviceTelemetry, Presence
from services.devices.telemetry import MAX_BATCH, RATE_LIMIT
from services.mixer import Mixer


class Clock:
    now = 1_000_000

    def __call__(self):
        return self.now


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    mixer = Mixer(mic=FakeMic())
    mixer.start()
    store = DeviceStore(path=str(tmp_path / "devices.json"), token_days=90, clock=clock)
    presence = Presence()
    telemetry = DeviceTelemetry(str(tmp_path / "logs"), clock=clock, max_events=20)
    device, token = store.create("Aula", "192.168.1.50")
    app = device_server.create_app(mixer, store, presence, telemetry)

    admin_app = FastAPI()
    admin_app.include_router(api_devices.create_router(SimpleNamespace(devices=store, presence=presence, telemetry=telemetry)))

    yield SimpleNamespace(
        mixer=mixer, store=store, telemetry=telemetry, device=device, token=token, clock=clock, tmp=tmp_path,
        client=TestClient(app, client=("192.168.1.50", 50000)),
        other_ip=TestClient(app, client=("192.168.1.99", 50000)),
        admin=TestClient(admin_app),
        headers={"authorization": f"Bearer {token}"},
    )
    mixer.stop()


def test_buffer_post_is_stored_and_visible_for_admin(env):
    r = env.client.post("/api/device/buffer", json={"fill_ms": 420, "capacity_ms": 1000, "underruns": 2, "drops": 0, "x": 1},
                        headers=env.headers)
    assert r.status_code == 200
    state = env.admin.get(f"/api/devices/{env.device.id}/buffer").json()["buffer"]
    assert state == {"fill_ms": 420, "capacity_ms": 1000, "underruns": 2, "drops": 0, "received_at": 1_000_000}
    listed = env.admin.get("/api/devices").json()["devices"][0]
    assert listed["buffer"]["fill_ms"] == 420


def test_newer_buffer_post_replaces_older(env):
    env.client.post("/api/device/buffer", json={"fill_ms": 1}, headers=env.headers)
    env.clock.now += 5
    env.client.post("/api/device/buffer", json={"fill_ms": 2}, headers=env.headers)
    state = env.telemetry.get_buffer(env.device.id)
    assert state["fill_ms"] == 2 and state["received_at"] == 1_000_005


@pytest.mark.parametrize("path, body", [
    ("/api/device/buffer", {"fill_ms": 1}),
    ("/api/device/log", {"events": [{"type": "drop"}]}),
])
def test_token_is_required(env, path, body):
    assert env.client.post(path, json=body).status_code == 401
    assert env.client.post(path, json=body, headers={"authorization": "Bearer hibas"}).status_code == 401
    assert env.client.post(path, json=body, headers={"authorization": "Basic abc"}).status_code == 401
    assert env.other_ip.post(path, json=body, headers=env.headers).status_code == 403  # nem a regisztralt IP
    env.store.update(env.device.id, enabled=False)
    assert env.client.post(path, json=body, headers=env.headers).status_code == 403
    assert env.telemetry.get_buffer(env.device.id) is None and env.telemetry.read(env.device.id) == []


@pytest.mark.parametrize("body", [
    [], "szoveg", {}, {"fill_ms": -1}, {"fill_ms": "10"}, {"fill_ms": True}, {"fill_ms": 1.5}, {"fill_ms": 1, "drops": "x"},
    {"fill_ms": 10**9},
])
def test_invalid_buffer_is_rejected(env, body):
    assert env.client.post("/api/device/buffer", json=body, headers=env.headers).status_code == 400
    assert env.telemetry.get_buffer(env.device.id) is None


def test_non_json_body_is_rejected(env):
    r = env.client.post("/api/device/buffer", content=b"{nem json", headers=env.headers)
    assert r.status_code == 400


def test_oversized_body_is_rejected(env):
    r = env.client.post("/api/device/log", content=b"x" * 20000, headers=env.headers)
    assert r.status_code == 413


def test_log_post_is_stored_with_server_time_and_filtered(env):
    r = env.client.post("/api/device/log", headers=env.headers, json={"events": [
        {"type": "drop", "msg": "csomag elveszett", "ts": 12},
        {"type": "wifi_lost"},
    ]})
    assert r.json() == {"ok": True, "accepted": 2}
    env.clock.now += 10
    env.client.post("/api/device/log", headers=env.headers, json={"events": [{"type": "drop"}]})
    events = env.admin.get(f"/api/devices/{env.device.id}/log").json()["events"]
    assert [e["type"] for e in events] == ["drop", "wifi_lost", "drop"]
    assert events[0] == {"type": "drop", "msg": "csomag elveszett", "device_ts": 12, "ts": 1_000_000, "source": "esp"}
    drops = env.admin.get(f"/api/devices/{env.device.id}/log", params={"type": "drop"}).json()["events"]
    assert len(drops) == 2
    recent = env.admin.get(f"/api/devices/{env.device.id}/log", params={"since": 1_000_005}).json()["events"]
    assert len(recent) == 1
    assert len(env.admin.get(f"/api/devices/{env.device.id}/log", params={"limit": 2}).json()["events"]) == 2


@pytest.mark.parametrize("body", [
    {}, {"events": []}, {"events": "x"}, {"events": [1]}, {"events": [{}]}, {"events": [{"type": "Nagy Betu"}]},
    {"events": [{"type": "drop", "msg": "sor\ntores"}]}, {"events": [{"type": "drop", "msg": 5}]},
    {"events": [{"type": "drop", "ts": "ma"}]}, {"events": [{"type": "drop"}] * (MAX_BATCH + 1)},
    {"events": [{"type": "drop"}, {"type": "!!"}]},
])
def test_invalid_log_is_rejected_whole(env, body):
    assert env.client.post("/api/device/log", json=body, headers=env.headers).status_code == 400
    assert env.telemetry.read(env.device.id) == []


def test_log_is_bounded_and_survives_restart(env):
    for i in range(30):
        env.telemetry.add(env.device.id, "esp", [{"type": "drop", "msg": str(i)}])
    events = env.telemetry.read(env.device.id, limit=1000)
    assert len(events) == 20 and events[-1]["msg"] == "29"
    reloaded = DeviceTelemetry(str(env.tmp / "logs"), clock=env.clock, max_events=20)
    again = reloaded.read(env.device.id, limit=1000)
    assert [e["msg"] for e in again] == [e["msg"] for e in events]
    with open(env.tmp / "logs" / f"{env.device.id}.jsonl", encoding="utf-8") as fh:
        assert len(fh.readlines()) <= 40  # a fajl sem nohet vegtelenre


def test_corrupt_log_line_is_skipped(env):
    env.telemetry.add(env.device.id, "esp", [{"type": "drop"}])
    with open(env.tmp / "logs" / f"{env.device.id}.jsonl", "a", encoding="utf-8") as fh:
        fh.write('{"type": "megszakad\n')
    fresh = DeviceTelemetry(str(env.tmp / "logs"), clock=env.clock)
    assert [e["type"] for e in fresh.read(env.device.id)] == ["drop"]


def test_rate_limit_per_device(env):
    codes = [env.client.post("/api/device/buffer", json={"fill_ms": 1}, headers=env.headers).status_code
             for _ in range(RATE_LIMIT + 1)]
    assert codes[:RATE_LIMIT] == [200] * RATE_LIMIT and codes[-1] == 429
    env.clock.now += 61
    assert env.client.post("/api/device/buffer", json={"fill_ms": 1}, headers=env.headers).status_code == 200


def test_stream_connect_and_disconnect_are_logged_with_offline_time(env):
    def session():
        with env.client.websocket_connect("/api/mixer/stream", headers=env.headers) as ws:
            ws.receive_json()
    session()
    assert wait_until(lambda: any(e["type"] == "disconnected" for e in env.telemetry.read(env.device.id)))
    env.clock.now += 90
    session()
    assert wait_until(lambda: [e["type"] for e in env.telemetry.read(env.device.id)].count("disconnected") == 2)
    events = env.telemetry.read(env.device.id)
    connects = [e for e in events if e["type"] == "connected"]
    assert "offline_seconds" not in connects[0] and connects[1]["offline_seconds"] == 90
    assert all(e["source"] == "server" for e in events)


def test_esp_cannot_pass_itself_off_as_server(env):
    env.client.post("/api/device/log", headers=env.headers, json={"events": [{"type": "disconnected"}]})
    assert env.telemetry.read(env.device.id)[0]["source"] == "esp"


def test_admin_log_and_buffer_for_unknown_device_is_404(env):
    assert env.admin.get("/api/devices/d_nincs/log").status_code == 404
    assert env.admin.get("/api/devices/d_nincs/buffer").status_code == 404


def test_deleting_device_removes_its_telemetry(env):
    env.telemetry.add(env.device.id, "esp", [{"type": "drop"}])
    assert env.admin.delete(f"/api/devices/{env.device.id}").status_code == 200
    assert not os.path.exists(env.tmp / "logs" / f"{env.device.id}.jsonl")
    assert env.telemetry.read(env.device.id) == []


def test_log_does_not_contain_the_token(env):
    env.client.post("/api/device/log", headers=env.headers, json={"events": [{"type": "drop"}]})
    with open(env.tmp / "logs" / f"{env.device.id}.jsonl", encoding="utf-8") as fh:
        assert env.token not in fh.read()
    json.loads(open(env.tmp / "logs" / f"{env.device.id}.jsonl", encoding="utf-8").readline())
