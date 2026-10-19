import json
import os
import socket
import sys
import time
import uuid as uuid_lib
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import devices as api_devices
from services.devices import DeviceConflict, DeviceInvalid, DeviceNotFound, DeviceStore, EnrollListener, Enrollment, Presence
from services.devices.enroll import parse_uuid5, sign, verify

KEY = "k" * 32
NS = uuid_lib.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


class Clock:
    now = 1_000_000.0

    def __call__(self):
        return self.now


def make_uuid(name="aa:bb:cc:dd:ee:01"):
    return str(uuid_lib.uuid5(NS, name))


def beacon(uuid=None, ver="1.2", ts=None, key=KEY, clock=None):
    uuid = uuid or make_uuid()
    ts = int(clock.now if ts is None else ts)
    return json.dumps({"uuid": uuid, "ver": ver, "ts": ts, "sig": sign(key, uuid, ver, ts)}).encode()


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    store = DeviceStore(path=str(tmp_path / "devices.json"), token_days=90, clock=clock)
    enrollment = Enrollment(store, KEY, "192.168.1.0/24", clock=clock)
    return SimpleNamespace(store=store, enrollment=enrollment, clock=clock)


def test_parse_uuid5_accepts_only_canonical_v5():
    good = make_uuid()
    assert parse_uuid5(good) == good
    assert parse_uuid5(str(uuid_lib.uuid4())) is None
    assert parse_uuid5(good.upper()) is None
    assert parse_uuid5(good.replace("-", "")) is None
    assert parse_uuid5(5) is None


def test_verify_rejects_bad_input():
    clock = Clock()
    ok, reason = verify(beacon(clock=clock), KEY, clock.now)
    assert reason == "" and ok["uuid"] == make_uuid() and ok["version"] == "1.2"
    assert verify(beacon(key="x" * 32, clock=clock), KEY, clock.now)[1] == "bad_signature"
    assert verify(beacon(ts=clock.now - 61, clock=clock), KEY, clock.now)[1] == "stale"
    assert verify(beacon(ts=clock.now + 61, clock=clock), KEY, clock.now)[1] == "stale"
    assert verify(beacon(uuid=str(uuid_lib.uuid4()), clock=clock), KEY, clock.now)[1] == "bad_uuid"
    assert verify(b"nem json", KEY, clock.now)[1] == "malformed"
    assert verify(b"[]", KEY, clock.now)[1] == "malformed"
    assert verify(b"x" * 600, KEY, clock.now)[1] == "too_large"
    tampered = json.loads(beacon(clock=clock))
    tampered["ver"] = "9.9"
    assert verify(json.dumps(tampered).encode(), KEY, clock.now)[1] == "bad_signature"
    flag = json.loads(beacon(clock=clock))
    flag["ts"] = True
    assert verify(json.dumps(flag).encode(), KEY, clock.now)[1] == "malformed"


def test_valid_beacon_becomes_pending(env):
    assert env.enrollment.handle(beacon(clock=env.clock), "192.168.1.50")
    [entry] = env.enrollment.list()
    assert entry["uuid"] == make_uuid() and entry["ip_address"] == "192.168.1.50"
    assert entry["version"] == "1.2" and entry["conflict"] is None


def test_disabled_without_long_key(env):
    short = Enrollment(env.store, "rovid", clock=env.clock)
    assert not short.enabled
    assert not short.handle(beacon(key="rovid", clock=env.clock), "192.168.1.50")
    assert short.list() == []


def test_source_outside_cidr_is_ignored(env):
    assert not env.enrollment.handle(beacon(clock=env.clock), "10.0.0.5")
    assert env.enrollment.list() == []


def test_default_accepts_private_only(env):
    open_net = Enrollment(env.store, KEY, clock=env.clock)
    assert open_net.handle(beacon(clock=env.clock), "10.1.2.3")
    assert not open_net.handle(beacon(uuid=make_uuid("x"), clock=env.clock), "8.8.8.8")


def test_replayed_datagram_is_rejected(env):
    data = beacon(clock=env.clock)
    assert env.enrollment.handle(data, "192.168.1.50")
    assert not env.enrollment.handle(data, "192.168.1.60")
    [entry] = env.enrollment.list()
    assert entry["ip_address"] == "192.168.1.50" and not entry["ip_changed"]


def test_same_uuid_from_other_ip_is_conflict(env):
    env.enrollment.handle(beacon(ts=env.clock.now, clock=env.clock), "192.168.1.50")
    env.clock.now += 1
    env.enrollment.handle(beacon(clock=env.clock), "192.168.1.60")
    [entry] = env.enrollment.list()
    assert entry["conflict"] == "ip_changed"
    with pytest.raises(DeviceConflict):
        env.enrollment.approve(entry["uuid"], "Aula")
    env.enrollment.dismiss(entry["uuid"])
    assert env.enrollment.list() == []


def test_approve_creates_device_with_uuid_and_token(env):
    env.enrollment.handle(beacon(clock=env.clock), "192.168.1.50")
    device, token = env.enrollment.approve(make_uuid(), "Aula")
    assert token and device.uuid == make_uuid() and device.ip_address == "192.168.1.50" and device.version == "1.2"
    assert env.store.find_by_uuid(make_uuid()).name == "Aula"
    assert env.enrollment.list() == []


def test_known_device_from_same_ip_is_not_pending(env):
    env.enrollment.handle(beacon(clock=env.clock), "192.168.1.50")
    env.enrollment.approve(make_uuid(), "Aula")
    env.clock.now += 1
    env.enrollment.handle(beacon(clock=env.clock), "192.168.1.50")
    assert env.enrollment.list() == []


def test_known_uuid_from_other_ip_is_conflict_and_never_overwrites(env):
    env.enrollment.handle(beacon(clock=env.clock), "192.168.1.50")
    env.enrollment.approve(make_uuid(), "Aula")
    env.clock.now += 1
    env.enrollment.handle(beacon(clock=env.clock), "192.168.1.99")
    [entry] = env.enrollment.list()
    assert entry["conflict"] == "uuid_known"
    with pytest.raises(DeviceConflict):
        env.enrollment.approve(entry["uuid"], "Masik")
    assert env.store.find_by_uuid(make_uuid()).ip_address == "192.168.1.50"


def test_ip_taken_by_other_device_is_conflict(env):
    env.store.create("Kezi", "192.168.1.50")
    env.enrollment.handle(beacon(clock=env.clock), "192.168.1.50")
    [entry] = env.enrollment.list()
    assert entry["conflict"] == "ip_taken"


def test_pending_expires_and_is_capped(env):
    env.enrollment.handle(beacon(clock=env.clock), "192.168.1.50")
    env.clock.now += 601
    assert env.enrollment.list() == []
    for i in range(70):
        env.enrollment.handle(beacon(uuid=make_uuid(f"m{i}"), clock=env.clock), f"192.168.1.{i + 1}")
    assert len(env.enrollment.list()) == 64


def test_approve_unknown_and_invalid_name(env):
    with pytest.raises(DeviceNotFound):
        env.enrollment.approve(make_uuid(), "Aula")
    env.enrollment.handle(beacon(clock=env.clock), "192.168.1.50")
    with pytest.raises(DeviceInvalid):
        env.enrollment.approve(make_uuid(), "")
    assert len(env.enrollment.list()) == 1  # sikertelen jovahagyas nem torli a jelentkezot


def test_store_rejects_bad_and_duplicate_uuid(env):
    with pytest.raises(DeviceInvalid):
        env.store.create("A", "192.168.1.10", uuid=str(uuid_lib.uuid4()))
    env.store.create("A", "192.168.1.10", uuid=make_uuid())
    with pytest.raises(DeviceConflict):
        env.store.create("B", "192.168.1.11", uuid=make_uuid())


def test_uuid_survives_reload(env, tmp_path):
    env.store.create("A", "192.168.1.10", uuid=make_uuid())
    reloaded = DeviceStore(path=env.store._path, clock=env.clock)
    assert reloaded.find_by_uuid(make_uuid()).name == "A"


def test_udp_listener_end_to_end(tmp_path):
    store = DeviceStore(path=str(tmp_path / "devices.json"))
    enrollment = Enrollment(store, KEY)  # valos ora
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    listener = EnrollListener(enrollment, "127.0.0.1", port)
    assert listener.start()
    try:
        data = json.loads(beacon(ts=int(time.time())))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.sendto(json.dumps(data).encode(), ("127.0.0.1", port))
        for _ in range(40):
            if enrollment.list():
                break
            time.sleep(0.05)
        [entry] = enrollment.list()
        assert entry["uuid"] == make_uuid() and entry["ip_address"] == "127.0.0.1"
    finally:
        listener.stop()


def test_listener_does_not_start_without_key(tmp_path):
    store = DeviceStore(path=str(tmp_path / "devices.json"))
    assert not EnrollListener(Enrollment(store, ""), "127.0.0.1", 0).start()


@pytest.fixture
def api(env):
    ctx = SimpleNamespace(devices=env.store, presence=Presence(), enrollment=env.enrollment)
    app = FastAPI()
    app.include_router(api_devices.create_router(ctx))
    return SimpleNamespace(client=TestClient(app), **env.__dict__)


def test_api_pending_approve_flow(api):
    api.enrollment.handle(beacon(clock=api.clock), "192.168.1.50")
    listing = api.client.get("/api/devices/pending").json()
    assert listing["enabled"] is True and listing["pending"][0]["uuid"] == make_uuid()
    r = api.client.post(f"/api/devices/pending/{make_uuid()}/approve", json={"name": "Aula"})
    assert r.status_code == 200 and r.json()["token"] and r.json()["device"]["uuid"] == make_uuid()
    assert "token_hash" not in r.text
    assert api.client.get("/api/devices/pending").json()["pending"] == []
    assert [d["name"] for d in api.client.get("/api/devices").json()["devices"]] == ["Aula"]


def test_api_pending_errors(api):
    assert api.client.post(f"/api/devices/pending/{make_uuid()}/approve", json={"name": "A"}).status_code == 404
    assert api.client.delete(f"/api/devices/pending/{make_uuid()}").status_code == 404
    api.enrollment.handle(beacon(clock=api.clock), "192.168.1.50")
    assert api.client.post(f"/api/devices/pending/{make_uuid()}/approve", json={"name": ""}).status_code == 400
    assert api.client.delete(f"/api/devices/pending/{make_uuid()}").json() == {"ok": True}


def test_api_pending_without_enrollment():
    ctx = SimpleNamespace(devices=None, presence=Presence(), enrollment=None)
    app = FastAPI()
    app.include_router(api_devices.create_router(ctx))
    client = TestClient(app)
    assert client.get("/api/devices/pending").json() == {"enabled": False, "pending": []}
