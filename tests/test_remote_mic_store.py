import json
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import services.remote_mic.store as store_module
from services.remote_mic.store import (
    RemoteMicConflict,
    RemoteMicInvalid,
    RemoteMicNotFound,
    RemoteMicStore,
    public_view,
)


class Clock:
    now = 1_000_000

    def __call__(self):
        return self.now


GOOD_TOKEN = "A" * 43


@pytest.fixture
def path(tmp_path):
    return str(tmp_path / "remote_mics.json")


@pytest.fixture
def store(path):
    return RemoteMicStore(path=path, clock=Clock())


def test_create_returns_usable_source_and_credential(store):
    source, token = store.create("Igazgatói", "192.168.1.60")
    assert source.port == 8770 and source.volume == 1.0
    assert source.created_at == 1_000_000 and source.rotated_at == 1_000_000
    assert source.token == token and len(token) == 43


def test_sources_survive_a_restart(path, store):
    source, token = store.create("Igazgatói", "192.168.1.60", 8771)
    reloaded = RemoteMicStore(path=path, clock=Clock())
    assert reloaded.get(source.id) == source
    assert reloaded.get(source.id).token == token


def test_public_view_hides_the_credential(store):
    source, token = store.create("Igazgatói", "192.168.1.60")
    view = public_view(source)
    assert "token" not in view and token not in json.dumps(view)
    assert view["name"] == "Igazgatói" and view["host"] == "192.168.1.60"


def test_get_returns_a_copy(store):
    source, _ = store.create("A", "10.0.0.1")
    store.get(source.id).name = "átírva"
    assert store.get(source.id).name == "A"


@pytest.mark.parametrize("name", ["", "   ", None, 5, "a" * 65, "sor\ntörés"])
def test_create_rejects_bad_name(store, name):
    with pytest.raises(RemoteMicInvalid):
        store.create(name, "10.0.0.1")


@pytest.mark.parametrize("host", ["", None, 5, "0.0.0.0", "999.1.1.1", "rossz host", "-x.local", "a/b", "ws://x"])
def test_create_rejects_bad_host(store, host):
    with pytest.raises(RemoteMicInvalid):
        store.create("A", host)


@pytest.mark.parametrize("host,stored", [("igazgato-pc.local", "igazgato-pc.local"), (" 10.0.0.9 ", "10.0.0.9"), ("::1", "::1")])
def test_create_accepts_ip_and_hostname(store, host, stored):
    source, _ = store.create("A", host)
    assert source.host == stored


@pytest.mark.parametrize("port", [0, 65536, -1, True, "8770", 80.5, None])
def test_create_rejects_bad_port(store, port):
    with pytest.raises(RemoteMicInvalid):
        store.create("A", "10.0.0.1", port)


def test_same_address_is_a_conflict_but_other_port_is_not(store):
    store.create("A", "10.0.0.1", 8770)
    with pytest.raises(RemoteMicConflict):
        store.create("B", "10.0.0.1", 8770)
    store.create("C", "10.0.0.1", 8771)
    assert len(store.list()) == 2


def test_at_most_eight_sources(store):
    for index in range(8):
        store.create(f"G{index}", f"10.0.0.{index + 1}")
    with pytest.raises(RemoteMicConflict):
        store.create("Kilencedik", "10.0.0.99")
    assert len(store.list()) == 8


def test_update_changes_address_and_keeps_credential(path, store):
    source, token = store.create("A", "10.0.0.1")
    store.update(source.id, name="Új", host="10.0.0.2", port=9000)
    reloaded = RemoteMicStore(path=path, clock=Clock()).get(source.id)
    assert (reloaded.name, reloaded.host, reloaded.port, reloaded.token) == ("Új", "10.0.0.2", 9000, token)


def test_update_rejects_unknown_field_and_other_sources_address(store):
    first, _ = store.create("A", "10.0.0.1")
    second, _ = store.create("B", "10.0.0.2")
    with pytest.raises(RemoteMicInvalid):
        store.update(first.id, token=GOOD_TOKEN)
    with pytest.raises(RemoteMicConflict):
        store.update(second.id, host="10.0.0.1")
    store.update(first.id, host="10.0.0.1")  # a saját címe nem ütközés


@pytest.mark.parametrize("volume,stored", [(0, 0.0), (0.25, 0.25), (1, 1.0)])
def test_set_volume_persists(path, store, volume, stored):
    source, _ = store.create("A", "10.0.0.1")
    store.set_volume(source.id, volume)
    assert RemoteMicStore(path=path, clock=Clock()).get(source.id).volume == stored


@pytest.mark.parametrize("volume", [float("nan"), float("inf"), -0.1, 1.1, True, "1", None])
def test_set_volume_rejects_bad_values(store, volume):
    source, _ = store.create("A", "10.0.0.1")
    with pytest.raises(RemoteMicInvalid):
        store.set_volume(source.id, volume)
    assert store.get(source.id).volume == 1.0


def test_set_credential_updates_rotation_time_durably(path):
    clock = Clock()
    store = RemoteMicStore(path=path, clock=clock)
    source, _ = store.create("A", "10.0.0.1")
    clock.now += 500
    store.set_credential(source.id, GOOD_TOKEN)
    reloaded = RemoteMicStore(path=path, clock=clock).get(source.id)
    assert reloaded.token == GOOD_TOKEN and reloaded.rotated_at == 1_000_500


def test_set_credential_rejects_malformed_value(store):
    source, token = store.create("A", "10.0.0.1")
    with pytest.raises(RemoteMicInvalid):
        store.set_credential(source.id, "rövid")
    assert store.get(source.id).token == token


def test_reissue_returns_a_new_credential(path):
    clock = Clock()
    store = RemoteMicStore(path=path, clock=clock)
    source, token = store.create("A", "10.0.0.1")
    clock.now += 7
    updated, fresh = store.reissue(source.id)
    assert fresh != token and updated.token == fresh and updated.rotated_at == 1_000_007
    assert RemoteMicStore(path=path, clock=clock).get(source.id).token == fresh


def test_delete_removes_durably(path, store):
    source, _ = store.create("A", "10.0.0.1")
    store.delete(source.id)
    assert store.get(source.id) is None
    assert RemoteMicStore(path=path, clock=Clock()).list() == []


@pytest.mark.parametrize("call", [
    lambda s: s.update("nincs", name="X"),
    lambda s: s.delete("nincs"),
    lambda s: s.set_volume("nincs", 0.5),
    lambda s: s.set_credential("nincs", GOOD_TOKEN),
    lambda s: s.reissue("nincs"),
])
def test_unknown_id_raises_not_found(store, call):
    with pytest.raises(RemoteMicNotFound):
        call(store)


def test_touch_records_last_seen_and_ignores_unknown(store):
    source, _ = store.create("A", "10.0.0.1")
    store.touch(source.id)
    assert store.get(source.id).last_seen == 1_000_000
    store.touch("nincs")


def test_touch_survives_a_write_failure(store, monkeypatch):
    source, _ = store.create("A", "10.0.0.1")

    def fail(*args, **kwargs):
        raise OSError("tele a lemez")

    monkeypatch.setattr(store_module, "atomic_write_json", fail)
    store.touch(source.id)
    assert store.get(source.id).last_seen is None


def test_failed_write_leaves_memory_unchanged(store, monkeypatch):
    source, token = store.create("A", "10.0.0.1")

    def fail(*args, **kwargs):
        raise OSError("tele a lemez")

    monkeypatch.setattr(store_module, "atomic_write_json", fail)
    with pytest.raises(OSError):
        store.set_credential(source.id, GOOD_TOKEN)
    assert store.get(source.id).token == token


def _entry(**overrides):
    data = {"id": "rm_00000001", "name": "A", "host": "10.0.0.1", "port": 8770, "token": GOOD_TOKEN,
            "volume": 0.5, "created_at": 1, "rotated_at": 1, "last_seen": None}
    data.update(overrides)
    return data


def _write(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def test_loads_valid_entries(path):
    _write(path, [_entry()])
    source = RemoteMicStore(path=path, clock=Clock()).get("rm_00000001")
    assert source.volume == 0.5 and source.token == GOOD_TOKEN


@pytest.mark.parametrize("bad", [
    {"token": "rövid"},
    {"name": ""},
    {"host": "rossz host"},
    {"port": 0},
    {"volume": 1.5},
    {"created_at": "tegnap"},
])
def test_skips_invalid_entries_and_keeps_good_ones(path, bad):
    _write(path, [_entry(), _entry(id="rm_00000002", host="10.0.0.2", **bad) if "host" not in bad else _entry(id="rm_00000002", **bad)])
    store = RemoteMicStore(path=path, clock=Clock())
    assert [s.id for s in store.list()] == ["rm_00000001"]
    assert store.load_warnings


def test_skips_duplicate_id_and_address(path):
    _write(path, [_entry(), _entry(host="10.0.0.2"), _entry(id="rm_00000003")])
    store = RemoteMicStore(path=path, clock=Clock())
    assert [s.id for s in store.list()] == ["rm_00000001"]
    assert len(store.load_warnings) == 2


def test_corrupt_file_is_empty_and_backed_up_before_first_write(path):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{nem json")
    store = RemoteMicStore(path=path, clock=Clock())
    assert store.list() == [] and store.load_warnings
    store.create("A", "10.0.0.1")
    backups = [n for n in os.listdir(os.path.dirname(path)) if ".corrupt-" in n]
    assert len(backups) == 1
    with open(os.path.join(os.path.dirname(path), backups[0]), encoding="utf-8") as fh:
        assert fh.read() == "{nem json"


def test_warnings_never_contain_the_credential(path, caplog):
    _write(path, [_entry(name="")])
    with caplog.at_level(logging.WARNING, logger="csengetes"):
        store = RemoteMicStore(path=path, clock=Clock())
    assert GOOD_TOKEN not in caplog.text and GOOD_TOKEN not in " ".join(store.load_warnings)
