import json
import os

import pytest

from services.music.playlists import PlaylistStore


@pytest.fixture
def path(tmp_path):
    return str(tmp_path / "playlists.json")


@pytest.fixture
def store(path):
    return PlaylistStore(path)


def test_create_rename_delete(store):
    pl = store.create("Reggeli")
    assert pl["name"] == "Reggeli"
    assert pl["tracks"] == []
    assert len(pl["id"]) == 8
    assert store.get(pl["id"]) == pl

    renamed = store.rename(pl["id"], "  Este  ")
    assert renamed["name"] == "Este"
    assert store.get(pl["id"])["name"] == "Este"

    store.delete(pl["id"])
    assert store.list() == []
    with pytest.raises(KeyError):
        store.get(pl["id"])


def test_names_unique_case_insensitive_and_length(store):
    a = store.create("Zene")
    with pytest.raises(ValueError):
        store.create("zene")
    with pytest.raises(ValueError):
        store.create("   ")
    with pytest.raises(ValueError):
        store.create("x" * 61)
    with pytest.raises(ValueError):
        store.create(None)
    assert store.create("x" * 60)["name"] == "x" * 60

    # sajat nev kis-/nagybetus valtoztatasa megengedett
    assert store.rename(a["id"], "ZENE")["name"] == "ZENE"
    with pytest.raises(ValueError):
        store.rename(a["id"], "x" * 61)


def test_add_tracks_skips_duplicates_keeps_order(store):
    pl = store.create("Lista")
    store.add_tracks(pl["id"], ["a.mp3", "b.mp3"])
    result = store.add_tracks(pl["id"], ["b.mp3", "c.mp3", "a.mp3", "d.mp3"])
    assert result["tracks"] == ["a.mp3", "b.mp3", "c.mp3", "d.mp3"]

    with pytest.raises(ValueError):
        store.add_tracks(pl["id"], ["ok.mp3", ""])
    with pytest.raises(ValueError):
        store.add_tracks(pl["id"], ["ok.mp3", 5])
    assert store.get(pl["id"])["tracks"] == ["a.mp3", "b.mp3", "c.mp3", "d.mp3"]


def test_move_track_semantics(store):
    pl = store.create("Sorrend")
    store.add_tracks(pl["id"], ["a", "b", "c"])

    assert store.move_track(pl["id"], 0, 2)["tracks"] == ["b", "c", "a"]
    assert store.move_track(pl["id"], 2, 0)["tracks"] == ["a", "b", "c"]

    with pytest.raises(ValueError):
        store.move_track(pl["id"], 3, 0)
    with pytest.raises(ValueError):
        store.move_track(pl["id"], 0, -1)
    with pytest.raises(ValueError):
        store.move_track(pl["id"], 0, 3)
    assert store.get(pl["id"])["tracks"] == ["a", "b", "c"]


def test_remove_track_everywhere_and_containing(store):
    one = store.create("Egy")
    two = store.create("Ketto")
    store.add_tracks(one["id"], ["a", "b"])
    store.add_tracks(two["id"], ["b", "c"])

    assert store.playlists_containing("b") == ["Egy", "Ketto"]
    assert store.playlists_containing("c") == ["Ketto"]
    assert store.playlists_containing("nincs") == []

    assert store.remove_track(one["id"], "a")["tracks"] == ["b"]
    store.remove_track_everywhere("b")
    assert store.get(one["id"])["tracks"] == []
    assert store.get(two["id"])["tracks"] == ["c"]
    assert store.playlists_containing("b") == []


def test_persists_between_instances(store, path):
    first = store.create("Tartos")
    store.add_tracks(first["id"], ["x.mp3"])
    store.set_active(first["id"])
    second = store.create("Masodik")

    again = PlaylistStore(path)
    assert [p["name"] for p in again.list()] == ["Tartos", "Masodik"]
    assert again.get(first["id"])["tracks"] == ["x.mp3"]
    assert again.active == first["id"]
    assert again.get(second["id"])["name"] == "Masodik"
    assert not os.path.exists(path + ".tmp")


def test_corrupt_file_starts_empty(path):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{ez nem json")
    store = PlaylistStore(path)
    assert store.list() == []
    assert store.active is None


def test_wrong_shape_file_starts_empty(path):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(["nem", "objektum"], fh)
    assert PlaylistStore(path).list() == []


def test_first_playlist_becomes_active_and_delete_clears_it(store):
    assert store.active is None
    first = store.create("Elso")
    second = store.create("Masodik")
    assert store.active == first["id"]

    store.delete(first["id"])
    assert store.active is None

    store.set_active(second["id"])
    assert store.active == second["id"]
    store.set_active(None)
    assert store.active is None
    with pytest.raises(KeyError):
        store.set_active("nincs")


def test_unknown_id_raises_keyerror(store):
    with pytest.raises(KeyError):
        store.get("nincs")
    with pytest.raises(KeyError):
        store.rename("nincs", "Valami")
    with pytest.raises(KeyError):
        store.delete("nincs")
    with pytest.raises(KeyError):
        store.add_tracks("nincs", ["a"])
    with pytest.raises(KeyError):
        store.remove_track("nincs", "a")
    with pytest.raises(KeyError):
        store.move_track("nincs", 0, 0)


def test_returned_values_are_copies(store):
    pl = store.create("Masolat")
    store.add_tracks(pl["id"], ["a"])

    pl["tracks"].append("kulso")
    pl["name"] = "atnevezve"
    store.list()[0]["tracks"].clear()

    fresh = store.get(pl["id"])
    assert fresh["tracks"] == ["a"]
    assert fresh["name"] == "Masolat"


def test_failed_save_keeps_memory_unchanged(store, monkeypatch):
    pl = store.create("Marad")

    def broken_replace(src, dst):
        raise OSError("lemez tele")

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(OSError):
        store.add_tracks(pl["id"], ["uj.mp3"])
    assert store.get(pl["id"])["tracks"] == []
