import json
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.devices.store import (
    DeviceConflict,
    DeviceInvalid,
    DeviceNotFound,
    DeviceStore,
    hash_token,
    normalize_ip,
)


class Clock:
    def __init__(self, now=1_000_000):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    return DeviceStore(path=str(tmp_path / "devices.json"), token_days=90, clock=clock)


def test_create_returns_raw_token_and_stores_only_hash(store, tmp_path):
    device, token = store.create("Aula", "192.168.1.50")
    assert device.name == "Aula" and device.ip_address == "192.168.1.50" and device.enabled
    assert device.token_hash == hash_token(token)
    assert device.token_expires_at == 1_000_000 + 90 * 86400
    raw = (tmp_path / "devices.json").read_text(encoding="utf-8")
    assert token not in raw
    assert device.token_hash in raw


def test_find_by_token(store):
    device, token = store.create("Aula", "192.168.1.50")
    assert store.find_by_token(token).id == device.id
    assert store.find_by_token("hibas") is None
    assert store.find_by_token("") is None


def test_persistence_across_instances(store, tmp_path, clock):
    device, token = store.create("Aula", "192.168.1.50")
    again = DeviceStore(path=str(tmp_path / "devices.json"), token_days=90, clock=clock)
    assert again.find_by_token(token).id == device.id


def test_duplicate_ip_is_conflict_even_in_other_notation(store):
    store.create("A", "192.168.1.50")
    with pytest.raises(DeviceConflict):
        store.create("B", "192.168.1.50")
    with pytest.raises(DeviceConflict):
        store.create("C", "::ffff:192.168.1.50")


def test_update_to_other_devices_ip_is_conflict(store):
    store.create("A", "192.168.1.50")
    b, _ = store.create("B", "192.168.1.51")
    with pytest.raises(DeviceConflict):
        store.update(b.id, ip_address="192.168.1.50")
    assert store.update(b.id, ip_address="192.168.1.51").ip_address == "192.168.1.51"  # sajat IP-jenek nem utkozik


@pytest.mark.parametrize("value", ["", "nem-ip", "0.0.0.0", "::", "224.0.0.1", "999.1.1.1", None, 5])
def test_invalid_ip_rejected(store, value):
    with pytest.raises(DeviceInvalid):
        store.create("A", value)


def test_normalize_ip():
    assert normalize_ip("192.168.1.50") == "192.168.1.50"
    assert normalize_ip(" 192.168.1.50 ") == "192.168.1.50"
    assert normalize_ip("::ffff:192.168.1.50") == "192.168.1.50"
    assert normalize_ip("2001:DB8::1") == "2001:db8::1"
    assert normalize_ip("testclient") is None
    assert normalize_ip(None) is None


@pytest.mark.parametrize("name", ["", "   ", "a\nb", "a\rb", "a\x00b", "x" * 65, None, 7, "ok\x1b[31m"])
def test_invalid_name_rejected(store, name):
    # vezerlokarakter a nevben naplosor-hamisitast engedne
    with pytest.raises(DeviceInvalid):
        store.create(name, "192.168.1.50")


def test_invalid_optional_field_rejected(store):
    with pytest.raises(DeviceInvalid):
        store.create("A", "192.168.1.50", hostname="x\ny")
    with pytest.raises(DeviceInvalid):
        store.create("A", "192.168.1.50", version=5)


def test_update_fields_and_unknown_field(store):
    d, _ = store.create("A", "192.168.1.50")
    u = store.update(d.id, name="Aula 2", hostname="esp-aula", enabled=False)
    assert (u.name, u.hostname, u.enabled) == ("Aula 2", "esp-aula", False)
    with pytest.raises(DeviceInvalid):
        store.update(d.id, token_hash="x")
    with pytest.raises(DeviceInvalid):
        store.update(d.id, enabled="igen")
    with pytest.raises(DeviceNotFound):
        store.update("nincs", name="x")


def test_rotate_invalidates_old_token_and_resets_expiry(store, clock):
    d, old = store.create("A", "192.168.1.50")
    clock.now += 10 * 86400
    d2, new = store.rotate(d.id)
    assert new != old
    assert store.find_by_token(old) is None
    assert store.find_by_token(new).id == d.id
    assert d2.token_expires_at == clock.now + 90 * 86400
    assert not store.is_current(d.id, d.token_hash)
    assert store.is_current(d.id, d2.token_hash)


def test_is_current_covers_disable_delete_expiry(store, clock):
    d, _ = store.create("A", "192.168.1.50")
    assert store.is_current(d.id, d.token_hash)
    clock.now = d.token_expires_at  # a lejarat pillanataban mar ervenytelen
    assert not store.is_current(d.id, d.token_hash)
    clock.now = 1_000_000
    store.update(d.id, enabled=False)
    assert not store.is_current(d.id, d.token_hash)
    store.update(d.id, enabled=True)
    assert store.is_current(d.id, d.token_hash)
    store.delete(d.id)
    assert not store.is_current(d.id, d.token_hash)
    assert store.get(d.id) is None
    with pytest.raises(DeviceNotFound):
        store.delete(d.id)


def test_touch_sets_last_seen(store, clock):
    d, _ = store.create("A", "192.168.1.50")
    clock.now = 1_000_500
    store.touch(d.id)
    assert store.get(d.id).last_seen == 1_000_500
    store.touch("nincs")  # nem dob kivetelt


def test_expiring(store, clock):
    a, _ = store.create("A", "192.168.1.50")
    clock.now += 80 * 86400  # 10 nap van hatra
    b, _ = store.create("B", "192.168.1.51")  # friss token
    assert [d.id for d in store.expiring(14)] == [a.id]
    store.update(a.id, enabled=False)
    assert store.expiring(14) == []  # letiltottat nem jelzunk


def test_missing_file_starts_empty(tmp_path, clock):
    s = DeviceStore(path=str(tmp_path / "nincs.json"), clock=clock)
    assert s.list() == []


@pytest.mark.parametrize("content", ["{nem json", "[]x", '{"devices": []}', '"szoveg"', "42", ""])
def test_corrupt_file_fails_closed_and_warns(tmp_path, clock, caplog, content):
    path = tmp_path / "devices.json"
    path.write_text(content, encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="csengetes"):
        s = DeviceStore(path=str(path), clock=clock)
    assert s.list() == []
    assert s.find_by_token("barmi") is None
    assert any("devices.json" in r.getMessage() for r in caplog.records)


def test_bad_entries_skipped_good_kept(tmp_path, clock, caplog):
    good = {
        "id": "d_1", "name": "Jo", "ip_address": "10.0.0.5", "enabled": True,
        "token_hash": hash_token("h"), "token_expires_at": 9_999_999, "created_at": 1,
    }
    path = tmp_path / "devices.json"
    path.write_text(json.dumps([good, {"id": "d_2"}, "szoveg", 5]), encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="csengetes"):
        s = DeviceStore(path=str(path), clock=clock)
    assert [d.id for d in s.list()] == ["d_1"]
    assert any("kihagyva" in r.getMessage() for r in caplog.records)


def test_audit_log_lines_never_contain_token_or_hash(store, caplog):
    with caplog.at_level(logging.INFO, logger="csengetes"):
        d, token = store.create("Aula", "192.168.1.50")
        _, token2 = store.rotate(d.id)
        store.update(d.id, enabled=False)
        store.update(d.id, enabled=True)
        store.update(d.id, name="Aula 2")
        store.delete(d.id)
    text = "\n".join(r.getMessage() for r in caplog.records)
    for secret in (token, token2, d.token_hash, hash_token(token2)):
        assert secret not in text
    for event in ("created", "rotated", "disabled", "enabled", "updated", "deleted"):
        assert f"[device:Aula] {event}" in text or f"[device:Aula 2] {event}" in text
    assert text.count("[device:Aula") >= 6


def test_concurrent_creates_do_not_duplicate_ip(store):
    import threading

    results = []

    def make(i):
        try:
            store.create(f"D{i}", "192.168.1.60")
            results.append("ok")
        except DeviceConflict:
            results.append("conflict")

    threads = [threading.Thread(target=make, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count("ok") == 1 and results.count("conflict") == 7
    assert len(store.list()) == 1
