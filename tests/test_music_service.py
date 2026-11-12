import os
from types import SimpleNamespace

import pytest

import services.music.main as music_main
from services.music import MusicDownloadError, MusicService, MusicSession

URL = "https://youtu.be/abc"


def _service(**kw):
    return MusicService([URL], None, None, **kw)


def test_service_progress_is_updated_and_cleared(monkeypatch):
    seen = []
    svc = _service()

    class FakeDownload:
        def __init__(self, url, preferredquality=192, progress_cb=None):
            self.cb = progress_cb

        def __call__(self):
            self.cb({"status": "downloading", "percent": 40.0, "title": "Egy"})
            seen.append(dict(svc.progress))
            self.cb({"status": "finished", "percent": 100.0, "title": "Egy"})
            self.cb({"status": "downloading", "percent": 10.0, "title": "Ketto"})
            seen.append(dict(svc.progress))
            return [os.path.abspath("a.mp3")]

    monkeypatch.setattr(music_main, "MusicDownload", FakeDownload)

    assert svc.prepare() == [os.path.abspath("a.mp3")]
    assert seen[0] == {"percent": 40.0, "title": "Egy", "item": 1}
    assert seen[1] == {"percent": 10.0, "title": "Ketto", "item": 2}
    assert svc.progress is None


def test_service_progress_is_cleared_when_download_fails(monkeypatch):
    svc = _service()

    class FailingDownload:
        def __init__(self, url, preferredquality=192, progress_cb=None):
            self.cb = progress_cb

        def __call__(self):
            self.cb({"status": "downloading", "percent": 5.0, "title": "Egy"})
            raise MusicDownloadError("hiba")

    monkeypatch.setattr(music_main, "MusicDownload", FailingDownload)

    with pytest.raises(MusicDownloadError):
        svc.prepare()
    assert svc.progress is None


def test_session_status_exposes_progress():
    session = MusicSession()
    assert session.status()["progress"] is None

    svc = _service()
    svc.progress = {"percent": 25.0, "title": "Egy", "item": 1}
    session._current = svc

    assert session.status()["progress"] == {"percent": 25.0, "title": "Egy", "item": 1}
