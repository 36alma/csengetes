import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import limits
from services.music import download
from services.music.download import MusicDownload, MusicDownloadError

URL = "https://www.youtube.com/watch?v=abc"


class FakeYDL:
    INFO: dict = {}
    EVENTS: list = []
    created: list = []

    def __init__(self, opts):
        self.opts = opts
        FakeYDL.created.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=True):
        if download:
            for event in FakeYDL.EVENTS:
                for hook in self.opts.get("progress_hooks", []):
                    hook(event)
        return FakeYDL.INFO


@pytest.fixture
def fake(tmp_path, monkeypatch):
    monkeypatch.setattr(download, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(download.shutil, "which", lambda n: "ffmpeg")
    monkeypatch.setattr(download, "DOWNLOAD_DIR", str(tmp_path / "download"))
    monkeypatch.setattr(FakeYDL, "created", [])
    monkeypatch.delenv("CSENGETES_MAX_YT_MINUTES", raising=False)
    monkeypatch.delenv("CSENGETES_MAX_DOWNLOAD_MB", raising=False)
    return tmp_path


def use(monkeypatch, info, events=()):
    monkeypatch.setattr(FakeYDL, "INFO", info)
    monkeypatch.setattr(FakeYDL, "EVENTS", list(events))


def test_check_limits_ok(fake, monkeypatch):
    use(monkeypatch, {"title": "Szam", "duration": 300})
    items = MusicDownload([URL]).check_limits()
    assert len(items) == 1
    assert items[0]["title"] == "Szam"
    assert items[0]["duration_s"] == 300
    assert items[0]["est_bytes"] == 300 * 192 * 1000 // 8
    assert FakeYDL.created[-1].opts["skip_download"] is True


def test_check_limits_too_long_names_title_and_limit(fake, monkeypatch):
    use(monkeypatch, {"title": "Hosszu", "duration": 901})
    with pytest.raises(MusicDownloadError) as exc:
        MusicDownload([URL]).check_limits()
    assert "Hosszu" in str(exc.value)
    assert "15" in str(exc.value)
    # a video hossza m:ss, a maximum a beallitott percek
    assert str(exc.value) == "A(z) 'Hosszu' tul hosszu (15:01), a megengedett maximum 15 perc."


def test_check_limits_too_long_formats_minutes_and_seconds(fake, monkeypatch):
    monkeypatch.setenv("CSENGETES_MAX_YT_MINUTES", "120")
    use(monkeypatch, {"title": "Maraton", "duration": 7325.7})
    with pytest.raises(MusicDownloadError) as exc:
        MusicDownload([URL]).check_limits()
    assert str(exc.value) == "A(z) 'Maraton' tul hosszu (122:05), a megengedett maximum 120 perc."


def test_check_limits_live_stream_rejected(fake, monkeypatch):
    use(monkeypatch, {"title": "Radio", "is_live": True})
    with pytest.raises(MusicDownloadError, match="Radio"):
        MusicDownload([URL]).check_limits()


def test_check_limits_unknown_duration_rejected(fake, monkeypatch):
    use(monkeypatch, {"title": "Ismeretlen", "duration": None})
    with pytest.raises(MusicDownloadError, match="Ismeretlen"):
        MusicDownload([URL]).check_limits()


def test_check_limits_playlist_with_one_long_entry_rejected(fake, monkeypatch):
    use(
        monkeypatch,
        {
            "_type": "playlist",
            "entries": [
                {"title": "Rovid", "duration": 60},
                None,
                {"title": "Hosszu", "duration": 901},
            ],
        },
    )
    with pytest.raises(MusicDownloadError, match="Hosszu"):
        MusicDownload([URL]).check_limits()


def test_download_missing_requested_downloads_raises_clear_error(fake, monkeypatch):
    use(monkeypatch, {"title": "Nincs fajl", "duration": 10})
    with pytest.raises(MusicDownloadError, match="Nincs fajl"):
        MusicDownload([URL])()


def test_download_max_filesize_skip_gives_clear_error(fake, monkeypatch):
    # yt-dlp skipped the download above max_filesize: requested_downloads has no filepath.
    use(monkeypatch, {"title": "Big Song", "requested_downloads": [{"format_id": "251"}]})
    with pytest.raises(MusicDownloadError) as exc:
        MusicDownload([URL])()
    assert "Big Song" in str(exc.value)
    assert str(exc.value) != "'filepath'"


def test_download_removes_oversized_file(fake, monkeypatch):
    monkeypatch.setenv("CSENGETES_MAX_DOWNLOAD_MB", "0.0001")
    big = fake / "big.mp3"
    big.write_bytes(b"x" * 1024)
    use(monkeypatch, {"title": "Nagy", "requested_downloads": [{"filepath": str(big)}]})
    with pytest.raises(MusicDownloadError):
        MusicDownload([URL])()
    assert not big.exists()


def test_progress_hook_maps_percent(fake, monkeypatch):
    out = fake / "ok.mp3"
    out.write_bytes(b"x")
    events = [
        {"status": "downloading", "downloaded_bytes": 50, "total_bytes": 200, "filename": "a.webm"},
        {"status": "downloading", "downloaded_bytes": 100, "total_bytes_estimate": 400, "filename": "a.webm"},
        {"status": "downloading", "downloaded_bytes": 100, "filename": "a.webm"},
    ]
    use(monkeypatch, {"title": "Szam", "requested_downloads": [{"filepath": str(out)}]}, events)
    calls = []
    MusicDownload([URL], progress_cb=calls.append)()
    assert [c["percent"] for c in calls] == [25.0, 25.0, None]
    assert calls[0] == {"status": "downloading", "percent": 25.0, "title": "a"}


def test_opts_contain_max_filesize(fake, monkeypatch):
    out = fake / "ok.mp3"
    out.write_bytes(b"x")
    use(monkeypatch, {"title": "Szam", "requested_downloads": [{"filepath": str(out)}]})
    MusicDownload([URL])()
    assert FakeYDL.created[-1].opts["max_filesize"] == limits.max_download_bytes()
