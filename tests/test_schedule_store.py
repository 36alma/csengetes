import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import schedule_store


def test_validate_accepts_correct_schedule():
    data = {"name": "Teszt", "events": [{"time": "07:45", "file": "x.mp3"}]}
    schedule_store.validate(data)  # nem szabad kivetelt dobnia


def test_validate_rejects_missing_events():
    with pytest.raises(schedule_store.ScheduleError):
        schedule_store.validate({"name": "Teszt"})


def test_validate_rejects_bad_time_format():
    data = {"name": "Teszt", "events": [{"time": "25:99", "file": "x.mp3"}]}
    with pytest.raises(schedule_store.ScheduleError):
        schedule_store.validate(data)


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule_store, "SCHEDULES_DIR", str(tmp_path))
    data = {"name": "Teszt", "events": [{"time": "08:00", "file": "a.mp3"}]}
    schedule_store.save("teszt.json", data)
    loaded = schedule_store.load("teszt.json")
    assert loaded == data


def test_list_schedules_only_json(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule_store, "SCHEDULES_DIR", str(tmp_path))
    (tmp_path / "a.json").write_text(json.dumps({"name": "a", "events": []}), encoding="utf-8")
    (tmp_path / "notes.txt").write_text("ignore me", encoding="utf-8")
    assert schedule_store.list_schedules() == ["a.json"]


def test_load_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule_store, "SCHEDULES_DIR", str(tmp_path / "schedules"))
    secret = tmp_path / "secret.json"
    secret.write_text(json.dumps({"name": "secret", "events": []}), encoding="utf-8")
    with pytest.raises(schedule_store.ScheduleError):
        schedule_store.load("../secret.json")


def test_save_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule_store, "SCHEDULES_DIR", str(tmp_path / "schedules"))
    data = {"name": "x", "events": []}
    with pytest.raises(schedule_store.ScheduleError):
        schedule_store.save("../evil.json", data)
    assert not (tmp_path / "evil.json").exists()


def test_save_rejects_absolute_path(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule_store, "SCHEDULES_DIR", str(tmp_path / "schedules"))
    data = {"name": "x", "events": []}
    outside = str(tmp_path / "outside.json")
    with pytest.raises(schedule_store.ScheduleError):
        schedule_store.save(outside, data)
    assert not (tmp_path / "outside.json").exists()
