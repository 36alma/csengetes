"""A devices.json sertes- es hibatuerese: kodolas, vesztesegmentes mentes, validalt betoltes, visszagordules."""

import glob
import json
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.devices import DeviceInvalid, DeviceStore, check, hash_token


class Clock:
    now = 1_000_000

    def __call__(self):
        return self.now


def entry(**over):
    data = {
        "id": "d_1", "name": "Jo", "ip_address": "10.0.0.5", "enabled": True,
        "token_hash": hash_token("tok"), "token_expires_at": 9_999_999_999, "created_at": 1,
    }
    data.update(over)
    return data


def backups(tmp_path):
    return sorted(glob.glob(str(tmp_path / "devices.json.corrupt-*")))


def test_non_utf8_file_fails_closed_instead_of_crashing(tmp_path, caplog):
    path = tmp_path / "devices.json"
    path.write_bytes('[{"id":"d_1","name":"Aula É"}]'.encode("cp1250"))
    with caplog.at_level(logging.WARNING, logger="csengetes"):
        store = DeviceStore(path=str(path), clock=Clock())
    assert store.list() == []
    assert any("devices.json" in r.getMessage() for r in caplog.records)


def test_partial_load_never_erases_original_without_backup(tmp_path):
    path = tmp_path / "devices.json"
    original = [entry(), {"id": "d_2"}]
    path.write_text(json.dumps(original), encoding="utf-8")
    store = DeviceStore(path=str(path), clock=Clock())
    store.touch("d_1")  # barmely iras felulirja a fajlt
    found = backups(tmp_path)
    assert len(found) == 1
    assert json.loads(open(found[0], encoding="utf-8").read()) == original
    store.touch("d_1")
    assert len(backups(tmp_path)) == 1  # csak egyszer keszul


def test_fully_corrupt_file_is_backed_up_before_first_write(tmp_path):
    path = tmp_path / "devices.json"
    path.write_text("{nem json", encoding="utf-8")
    store = DeviceStore(path=str(path), clock=Clock())
    store.create("Uj", "192.168.1.50")
    found = backups(tmp_path)
    assert len(found) == 1 and open(found[0], encoding="utf-8").read() == "{nem json"


def test_clean_file_makes_no_backup(tmp_path):
    path = tmp_path / "devices.json"
    path.write_text(json.dumps([entry()]), encoding="utf-8")
    store = DeviceStore(path=str(path), clock=Clock())
    store.touch("d_1")
    store.create("Masik", "192.168.1.50")
    assert backups(tmp_path) == []


def test_load_warnings_can_be_replayed_after_logging_is_configured(tmp_path, caplog):
    path = tmp_path / "devices.json"
    path.write_text("{nem json", encoding="utf-8")
    store = DeviceStore(path=str(path), clock=Clock())
    assert store.load_warnings
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="csengetes"):
        store.replay_load_warnings()
    assert any("devices.json" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    "bad",
    [
        entry(id="d_2", token_hash="é" * 64, ip_address="10.0.0.6"),
        entry(id="d_2", token_hash="abc", ip_address="10.0.0.6"),
        entry(id="d_2", token_hash=hash_token("x").upper(), ip_address="10.0.0.6"),
        entry(id="d_2", name="a\nb", ip_address="10.0.0.6", token_hash=hash_token("x")),
        entry(id="d_2", ip_address="::ffff:10.0.0.7", token_hash=hash_token("x")),
        entry(id="d_2", ip_address="nem-ip", token_hash=hash_token("x")),
        entry(id="d_1", ip_address="10.0.0.6", token_hash=hash_token("x")),  # ketszer ugyanaz az id
        entry(id="d_2", ip_address="10.0.0.5", token_hash=hash_token("x")),  # ketszer ugyanaz az IP
        entry(id="d_2", ip_address="10.0.0.6"),  # ketszer ugyanaz a token-hash
    ],
)
def test_invalid_loaded_entry_is_skipped_and_cannot_break_auth(tmp_path, caplog, bad):
    path = tmp_path / "devices.json"
    path.write_text(json.dumps([entry(), bad]), encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="csengetes"):
        store = DeviceStore(path=str(path), clock=Clock())
    assert [d.id for d in store.list()] == ["d_1"]
    assert any("kihagyva" in r.getMessage() for r in caplog.records)
    decision = check(store, "Bearer tok", "10.0.0.5")  # nem dob kivetelt
    assert decision.allowed


@pytest.mark.parametrize("name", ["a b", "a b", "a\u0085b", "a‮b", "\ud800", "a​b"])
def test_unicode_line_breaks_format_chars_and_surrogates_rejected(tmp_path, name):
    store = DeviceStore(path=str(tmp_path / "devices.json"), clock=Clock())
    with pytest.raises(DeviceInvalid):
        store.create(name, "192.168.1.50")
    assert store.list() == []


def test_accented_hungarian_names_are_still_fine(tmp_path):
    store = DeviceStore(path=str(tmp_path / "devices.json"), clock=Clock())
    d, _ = store.create("Áula őrü üvegtábla", "192.168.1.50")
    assert d.name.startswith("Áula")


def test_failed_save_rolls_back_every_write(tmp_path, monkeypatch):
    store = DeviceStore(path=str(tmp_path / "devices.json"), clock=Clock())
    device, old = store.create("A", "192.168.1.50")

    def boom(self, devices):
        raise OSError("lemez tele")

    monkeypatch.setattr(DeviceStore, "_save", boom)
    with pytest.raises(OSError):
        store.rotate(device.id)
    with pytest.raises(OSError):
        store.update(device.id, enabled=False, name="B")
    with pytest.raises(OSError):
        store.create("C", "192.168.1.51")
    with pytest.raises(OSError):
        store.delete(device.id)

    same = store.get(device.id)
    assert same is not None and same.token_hash == device.token_hash
    assert same.enabled is True and same.name == "A"
    assert store.find_by_token(old).id == device.id
    assert len(store.list()) == 1

    store.touch(device.id)  # a last_seen best-effort: nem dob kivetelt
    assert store.get(device.id).last_seen is None

    monkeypatch.undo()
    rotated, new = store.rotate(device.id)  # a hiba utan az irasok ujra mukodnek
    assert store.find_by_token(new).id == device.id and store.find_by_token(old) is None
    store.create("C", "192.168.1.51")
