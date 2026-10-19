import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.devices.auth import check, parse_bearer
from services.devices.presence import Presence
from services.devices.store import DeviceStore


class Clock:
    now = 1_000_000

    def __call__(self):
        return self.now


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    store = DeviceStore(path=str(tmp_path / "devices.json"), token_days=90, clock=clock)
    device, token = store.create("Aula", "192.168.1.50")
    return store, device, token, clock


@pytest.mark.parametrize(
    "header, expected",
    [
        ("Bearer abc", "abc"),
        ("bearer abc", "abc"),
        ("BEARER abc", "abc"),
        ("Bearer   abc  ", "abc"),
        ("Bearer", None),
        ("Bearer ", None),
        ("Bearer a b", None),
        ("Basic abc", None),
        ("abc", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_bearer(header, expected):
    assert parse_bearer(header) == expected


def test_allowed(env):
    store, device, token, _ = env
    d = check(store, f"Bearer {token}", "192.168.1.50")
    assert d.allowed and d.reason == "ok" and d.device.id == device.id


def test_ipv4_mapped_client_matches(env):
    store, _, token, _ = env
    assert check(store, f"Bearer {token}", "::ffff:192.168.1.50").allowed


@pytest.mark.parametrize("header", [None, "", "Bearer", "Bearer hibas", "Basic xyz"])
def test_bad_or_missing_token(env, header):
    store, *_ = env
    d = check(store, header, "192.168.1.50")
    assert (d.allowed, d.reason, d.device) == (False, "bad_token", None)


def test_disabled(env):
    store, device, token, _ = env
    store.update(device.id, enabled=False)
    d = check(store, f"Bearer {token}", "192.168.1.50")
    assert (d.allowed, d.reason) == (False, "disabled") and d.device.id == device.id


def test_expired(env):
    store, device, token, clock = env
    clock.now = device.token_expires_at
    assert check(store, f"Bearer {token}", "192.168.1.50").reason == "expired"


@pytest.mark.parametrize("ip", ["192.168.1.51", "testclient", "", None, "::1"])
def test_ip_mismatch(env, ip):
    store, device, token, _ = env
    d = check(store, f"Bearer {token}", ip)
    assert (d.allowed, d.reason) == (False, "ip_mismatch") and d.device.id == device.id


def test_order_disabled_before_expired_before_ip(env):
    store, device, token, clock = env
    clock.now = device.token_expires_at
    store.update(device.id, enabled=False)
    assert check(store, f"Bearer {token}", "10.9.9.9").reason == "disabled"
    store.update(device.id, enabled=True)
    assert check(store, f"Bearer {token}", "10.9.9.9").reason == "expired"


def test_old_token_after_rotation_is_bad(env):
    store, device, token, _ = env
    store.rotate(device.id)
    assert check(store, f"Bearer {token}", "192.168.1.50").reason == "bad_token"


def test_presence_counts_multiple_connections():
    p = Presence()
    assert not p.is_connected("d_1")
    p.enter("d_1")
    p.enter("d_1")
    p.leave("d_1")
    assert p.is_connected("d_1")
    p.leave("d_1")
    assert not p.is_connected("d_1")
    p.leave("d_1")  # tobbszori leave nem megy negativba
    p.enter("d_1")
    assert p.is_connected("d_1")


def test_presence_thread_safety():
    p = Presence()

    def work():
        for _ in range(500):
            p.enter("d_1")
            p.leave("d_1")

    threads = [threading.Thread(target=work) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not p.is_connected("d_1")
