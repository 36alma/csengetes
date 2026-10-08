import logging
import os
import socket
import sys
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fakes import FakeMic, wait_until
import device_server
from model import AudioPacket
from services.devices import DeviceStore, Presence
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
    device, token = store.create("Aula", "192.168.1.50")
    app = device_server.create_app(mixer, store, presence)

    def client_for(ip):
        return TestClient(app, client=(ip, 50000))

    yield type("Env", (), dict(mixer=mixer, store=store, presence=presence, device=device, token=token,
                               clock=clock, client_for=staticmethod(client_for), app=app))
    mixer.stop()


def _auth(token):
    return {"authorization": f"Bearer {token}"}


def test_allowed_device_streams_packets_and_is_logged(env, caplog):
    with caplog.at_level(logging.INFO, logger="csengetes"):
        with env.client_for("192.168.1.50").websocket_connect("/api/mixer/stream", headers=_auth(env.token)) as ws:
            assert ws.receive_json()["codec"] == "mp3"
            assert env.presence.is_connected(env.device.id)
            packets = [AudioPacket.from_bytes(ws.receive_bytes()) for _ in range(4)]
        assert wait_until(lambda: not env.presence.is_connected(env.device.id))
    assert [p.seq for p in packets] == list(range(packets[0].seq, packets[0].seq + 4))
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "[device:Aula] connected ip=192.168.1.50" in text
    assert "[device:Aula] disconnected reason=closed" in text
    assert env.token not in text
    assert env.store.get(env.device.id).last_seen == 1_000_000


@pytest.mark.parametrize(
    "ip, headers_fn, reason",
    [
        ("192.168.1.50", lambda e: {}, "bad_token"),
        ("192.168.1.50", lambda e: _auth("hibas"), "bad_token"),
        ("192.168.1.50", lambda e: {"authorization": "Basic abc"}, "bad_token"),
        ("192.168.1.99", lambda e: _auth(e.token), "ip_mismatch"),
    ],
)
def test_denied_clients_are_closed_and_logged(env, caplog, ip, headers_fn, reason):
    with caplog.at_level(logging.INFO, logger="csengetes"):
        with pytest.raises(WebSocketDisconnect) as exc:
            with env.client_for(ip).websocket_connect("/api/mixer/stream", headers=headers_fn(env)):
                pass
    assert exc.value.code == 1008
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert f"denied reason={reason} ip={ip}" in text
    assert not env.presence.is_connected(env.device.id)


def test_denied_disabled_and_expired(env, caplog):
    env.store.update(env.device.id, enabled=False)
    with pytest.raises(WebSocketDisconnect):
        with env.client_for("192.168.1.50").websocket_connect("/api/mixer/stream", headers=_auth(env.token)):
            pass
    env.store.update(env.device.id, enabled=True)
    env.clock.now = env.device.token_expires_at
    with caplog.at_level(logging.INFO, logger="csengetes"):
        with pytest.raises(WebSocketDisconnect):
            with env.client_for("192.168.1.50").websocket_connect("/api/mixer/stream", headers=_auth(env.token)):
                pass
    assert "[device:Aula] denied reason=expired" in "\n".join(r.getMessage() for r in caplog.records)


def test_unknown_token_is_logged_with_question_mark(env, caplog):
    with caplog.at_level(logging.INFO, logger="csengetes"):
        with pytest.raises(WebSocketDisconnect):
            with env.client_for("192.168.1.50").websocket_connect("/api/mixer/stream", headers=_auth("hibas")):
                pass
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "[device:?] denied reason=bad_token" in text
    assert "hibas" not in text


def test_only_stream_route_exists(env):
    client = env.client_for("192.168.1.50")
    for path in ("/", "/api/devices", "/api/mixer", "/api/logs", "/api/mixer/volume", "/app.js"):
        assert client.get(path, headers=_auth(env.token)).status_code == 404
    assert client.post("/api/mixer/mic", json={"live": True}, headers=_auth(env.token)).status_code == 404
    paths = {getattr(r, "path", None) for r in env.app.routes}
    assert paths == {"/api/mixer/stream"}


@pytest.mark.parametrize("action", ["rotate", "disable", "delete", "expire"])
def test_running_stream_is_cut_when_device_is_revoked(env, caplog, action):
    with caplog.at_level(logging.INFO, logger="csengetes"):
        with env.client_for("192.168.1.50").websocket_connect("/api/mixer/stream", headers=_auth(env.token)) as ws:
            ws.receive_json()
            ws.receive_bytes()
            if action == "rotate":
                env.store.rotate(env.device.id)
            elif action == "disable":
                env.store.update(env.device.id, enabled=False)
            elif action == "delete":
                env.store.delete(env.device.id)
            else:
                env.clock.now = env.device.token_expires_at
            with pytest.raises(WebSocketDisconnect) as exc:
                deadline = time.time() + 5
                while time.time() < deadline:
                    ws.receive_bytes()
            assert exc.value.code == 1008
        assert wait_until(lambda: not env.presence.is_connected(env.device.id))
    assert "disconnected reason=revoked" in "\n".join(r.getMessage() for r in caplog.records)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_real_socket_with_bearer_header(env):
    from websockets.sync.client import connect
    from websockets.exceptions import InvalidStatus

    env.store.update(env.device.id, ip_address="127.0.0.1")
    port = _free_port()
    server = device_server.DeviceServer(env.mixer, env.store, env.presence, "127.0.0.1", port)
    assert server.start() is True
    try:
        with connect(f"ws://127.0.0.1:{port}/api/mixer/stream", additional_headers=_auth(env.token)) as ws:
            assert '"mp3"' in ws.recv()
            assert isinstance(ws.recv(), bytes)
        with pytest.raises(InvalidStatus):
            with connect(f"ws://127.0.0.1:{port}/api/mixer/stream", additional_headers=_auth("hibas")):
                pass
    finally:
        server.stop()


def test_busy_port_is_logged_not_raised(env, caplog):
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        port = blocker.getsockname()[1]
        server = device_server.DeviceServer(env.mixer, env.store, env.presence, "127.0.0.1", port)
        with caplog.at_level(logging.ERROR, logger="csengetes"):
            assert server.start() is False
        server.stop()
    assert any("eszkozszerver" in r.getMessage() for r in caplog.records)
