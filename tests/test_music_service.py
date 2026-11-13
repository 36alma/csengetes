import os
from types import SimpleNamespace

import pytest

import services.music.main as music_main
from fakes import wait_until
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


# ---------- T8: sor, index, loop, tekeres ----------


class FakePlayer:
    """Mixer helyettes: a play() azonnal lefut, a hangot nem jatssza le."""

    def __init__(self, fail=(), position=None):
        self.fail = set(fail)
        self.played: list[str] = []
        self.loop = False
        self.loop_at_play: list[bool] = []
        self.seeks: list[float] = []
        self._position = position
        self.hook = None  # play() utan, on_done elott hivodik: hook(hanyadik_hivas)

    def play(self, path, blocking=False, force=False, on_done=None):
        name = os.path.basename(path)
        self.played.append(name)
        self.loop_at_play.append(self.loop)
        if self.hook is not None:
            self.hook(len(self.played))
        if on_done is not None:
            on_done("boom" if name in self.fail else None)

    def position(self):
        return self._position

    def seek(self, seconds):
        self.seeks.append(seconds)
        return seconds

    def stop(self):
        pass

    def set_loop(self, enabled):
        self.loop = bool(enabled)


def _svc(files, player=None, **kw):
    kw.setdefault("force", True)
    return MusicService([], player or FakePlayer(), None, files=files, **kw)


def test_plays_queue_in_order_then_done():
    player = FakePlayer()
    svc = _svc(["/m/a.mp3", "/m/b.mp3", "/m/c.mp3"], player)

    svc()

    assert wait_until(lambda: svc.status == "done")
    assert player.played == ["a.mp3", "b.mp3", "c.mp3"]
    assert svc.index is None and svc.current is None


def test_loop_playlist_wraps():
    player = FakePlayer()
    svc = _svc(["/m/a.mp3", "/m/b.mp3"], player, )
    svc.loop_mode = "playlist"
    player.hook = lambda n: svc.stop() if n == 5 else None

    svc()

    assert wait_until(lambda: svc.status == "stopped")
    assert player.played == ["a.mp3", "b.mp3", "a.mp3", "b.mp3", "a.mp3"]


def test_loop_track_sets_player_flag():
    player = FakePlayer()
    svc = _svc(["/m/a.mp3", "/m/b.mp3"], player)
    svc.loop_mode = "track"

    svc()

    assert wait_until(lambda: svc.status == "done")
    assert player.loop_at_play == [True, True]
    assert player.loop is False


def test_one_failing_track_is_skipped_and_queue_continues():
    player = FakePlayer(fail={"b.mp3"})
    svc = _svc(["/m/a.mp3", "/m/b.mp3", "/m/c.mp3"], player)

    svc()

    assert wait_until(lambda: svc.status == "done")
    assert player.played == ["a.mp3", "b.mp3", "c.mp3"]
    assert svc.error is None


def test_all_tracks_failing_ends_in_error_not_endless_loop():
    player = FakePlayer(fail={"a.mp3", "b.mp3"})
    svc = _svc(["/m/a.mp3", "/m/b.mp3"], player)
    svc.loop_mode = "playlist"

    svc()

    assert wait_until(lambda: svc.status == "error")
    assert len(player.played) == len(svc.filename)
    assert svc.error == "boom"


def test_single_failing_track_is_error():
    player = FakePlayer(fail={"a.mp3"})
    svc = _svc(["/m/a.mp3"], player)

    svc()

    assert wait_until(lambda: svc.status == "error")
    assert svc.error == "boom"


def test_next_track_cases():
    files = ["/m/a.mp3", "/m/b.mp3", "/m/c.mp3"]
    svc = _svc(files)

    svc.index = 0
    assert svc.next_track == files[1]
    svc.index = 2
    assert svc.next_track is None
    svc.loop_mode = "playlist"
    assert svc.next_track == files[0]
    svc.loop_mode = "track"
    assert svc.next_track == files[2]
    svc.index = None
    assert svc.next_track is None


def test_set_loop_rejects_unknown_mode():
    svc = _svc(["/m/a.mp3"])
    with pytest.raises(ValueError):
        svc.set_loop("shuffle")
    assert svc.loop_mode == "off"


def test_set_loop_applies_to_player_only_while_playing():
    player = FakePlayer()
    svc = _svc(["/m/a.mp3"], player)

    svc.set_loop("track")
    assert player.loop is False  # nem szol: csak mentodik

    svc.status = "playing"
    svc.set_loop("track")
    assert player.loop is True


def test_seek_by_uses_position_plus_delta():
    player = FakePlayer(position=(10.0, 100.0))
    svc = _svc(["/m/a.mp3"], player)
    svc.status = "playing"

    assert svc.seek_by(10) == 20.0
    assert player.seeks == [20.0]


def test_seek_by_without_position_is_none():
    player = FakePlayer(position=None)
    svc = _svc(["/m/a.mp3"], player)
    svc.status = "playing"

    assert svc.seek_by(10) is None
    assert player.seeks == []


def test_stop_resets_loop_flag():
    player = FakePlayer()
    svc = _svc(["/m/a.mp3"], player)
    svc.status = "playing"
    svc.index = 0
    player.loop = True

    svc.stop()

    assert player.loop is False
    assert svc.index is None


def test_session_status_idle_has_all_keys():
    session = MusicSession()
    session.set_loop("track")

    status = session.status()

    assert set(status) == {
        "status", "files", "queue", "index", "current", "next", "loop",
        "position_s", "duration_s", "progress", "error",
    }
    assert status["status"] == "idle"
    assert status["queue"] == []
    assert status["index"] is None
    assert status["loop"] == session.loop_mode == "track"
    assert status["progress"] is None


def test_session_status_reports_position_and_next_when_playing():
    player = FakePlayer(position=(12.5, 200.0))
    svc = _svc(["/m/a.mp3", "/m/b.mp3"], player)
    svc.status = "playing"
    svc.index, svc.current = 0, "/m/a.mp3"
    session = MusicSession()
    session._current = svc

    status = session.status()

    assert status["files"] == status["queue"] == ["a.mp3", "b.mp3"]
    assert status["index"] == 0
    assert status["current"] == "a.mp3"
    assert status["next"] == "b.mp3"
    assert status["position_s"] == 12.5
    assert status["duration_s"] == 200.0


def test_session_applies_loop_mode_to_new_service():
    session = MusicSession()
    session.set_loop("track")
    player = FakePlayer()
    svc = _svc(["/m/a.mp3", "/m/b.mp3"], player)

    assert session.start(svc) is True

    assert svc.loop_mode == "track"
    assert wait_until(lambda: svc.status == "done")
    assert player.loop_at_play == [True, True]


def test_session_set_loop_rejects_unknown_mode_and_seek_idle_is_none():
    session = MusicSession()
    with pytest.raises(ValueError):
        session.set_loop("shuffle")
    assert session.loop_mode == "off"
    assert session.seek(position_s=5) is None
    assert session.seek(delta_s=5) is None


def test_empty_queue_ends_as_done_without_playing():
    player = FakePlayer()
    svc = _svc([], player)

    svc._start_music()

    assert wait_until(lambda: svc.status == "done")
    assert player.played == []
    assert svc.index is None and svc.current is None
    assert player.loop is False


def test_session_set_loop_applies_to_finished_service():
    session = MusicSession()
    svc = _svc(["/m/a.mp3"])
    svc.status = "done"
    session._current = svc

    session.set_loop("playlist")

    assert svc.loop_mode == "playlist"
    assert session.status()["loop"] == "playlist"


def test_session_set_loop_applies_to_downloading_service_without_touching_player():
    session = MusicSession()
    player = FakePlayer()
    svc = _svc(["/m/a.mp3"], player)
    svc.status = "downloading"
    session._current = svc

    session.set_loop("track")

    assert svc.loop_mode == "track"
    assert player.loop is False


# ---------- R13: az ismetles nem lepheti at a kovetkezo csengetest, kiveve force-nal ----------


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class TimedPlayer(FakePlayer):
    """Mint a FakePlayer, de minden lejatszas a szam hosszaval leptet egy hamis orat."""

    def __init__(self, clock, lengths, **kw):
        super().__init__(**kw)
        self.clock = clock
        self.lengths = lengths

    def play(self, path, blocking=False, force=False, on_done=None):
        self.clock.t += self.lengths[os.path.basename(path)]
        super().play(path, blocking, force, on_done)


def _bounded(monkeypatch, files, lengths, seconds_left, loop, **kw):
    """Nem force szolgaltatas hamis ora + hamis hosszakkal; seconds_left: get_break() ertek."""
    clock = FakeClock()
    player = TimedPlayer(clock, lengths, **kw)
    monkeypatch.setattr(music_main, "track_duration", lambda p: lengths[os.path.basename(p)])
    svc = MusicService([], player, None, force=False, files=files, clock=clock)
    svc.get_break = lambda: seconds_left
    svc.loop_mode = loop
    return svc, player, clock


def test_non_force_playlist_loop_stops_before_the_deadline(monkeypatch):
    svc, player, clock = _bounded(
        monkeypatch, ["/m/a.mp3", "/m/b.mp3"], {"a.mp3": 10.0, "b.mp3": 10.0}, 70, "playlist"
    )

    assert svc() is True

    assert wait_until(lambda: svc.status == "done")
    # 20 mp-es sor 70 mp-ig: 3 kor fer bele (60 mp), a negyedik mar atlepne a csengetest
    assert player.played == ["a.mp3", "b.mp3"] * 3
    assert clock.t - 1000.0 <= 70
    assert svc.current is None and svc.index is None


def test_non_force_playlist_loop_still_loops_while_time_remains(monkeypatch):
    svc, player, clock = _bounded(
        monkeypatch, ["/m/a.mp3", "/m/b.mp3"], {"a.mp3": 10.0, "b.mp3": 10.0}, 1000, "playlist"
    )
    player.hook = lambda n: svc.stop() if n == 9 else None

    svc()

    assert wait_until(lambda: svc.status == "stopped")
    assert player.played == ["a.mp3", "b.mp3"] * 4 + ["a.mp3"]


def test_non_force_track_loop_does_not_start_a_repeat_crossing_the_deadline(monkeypatch):
    svc, player, clock = _bounded(monkeypatch, ["/m/a.mp3"], {"a.mp3": 10.0}, 35, "track")

    svc()

    assert wait_until(lambda: svc.status == "done")
    # 0-10, 10-20, 20-30 jo; a negyedik 30-40 atlepne a 35 mp-es hatart
    assert player.played == ["a.mp3"] * 3
    assert clock.t - 1000.0 <= 35
    # a hataros szamismetles nem a mixer hatar nelkuli (gapless) jelzojet hasznalja
    assert player.loop_at_play == [False, False, False]
    assert player.loop is False


def test_non_force_track_loop_repeats_only_the_current_track(monkeypatch):
    svc, player, clock = _bounded(
        monkeypatch, ["/m/a.mp3", "/m/b.mp3"], {"a.mp3": 10.0, "b.mp3": 10.0}, 45, "track"
    )

    svc()

    assert wait_until(lambda: svc.status == "done")
    # az elso szam ismetlodik (0-10 ... 30-40), amig belefer a 45 mp-be; a sor tobbi eleme nem kerul sorra
    assert player.played == ["a.mp3"] * 4


def test_non_force_track_loop_turned_off_mid_play_advances(monkeypatch):
    svc, player, clock = _bounded(
        monkeypatch, ["/m/a.mp3", "/m/b.mp3"], {"a.mp3": 10.0, "b.mp3": 10.0}, 1000, "track"
    )
    player.hook = lambda n: setattr(svc, "loop_mode", "off") if n == 2 else None

    svc()

    assert wait_until(lambda: svc.status == "done")
    assert player.played == ["a.mp3", "a.mp3", "b.mp3"]


def test_non_force_track_loop_does_not_repeat_a_failing_track(monkeypatch):
    svc, player, clock = _bounded(monkeypatch, ["/m/a.mp3"], {"a.mp3": 1.0}, 1000, "track", fail={"a.mp3"})

    svc()

    assert wait_until(lambda: svc.status == "error")
    assert player.played == ["a.mp3"]
    assert svc.error == "boom"


def test_non_force_repeat_with_unreadable_length_ends_done(monkeypatch):
    clock = FakeClock()
    player = TimedPlayer(clock, {"a.mp3": 10.0})
    monkeypatch.setattr(music_main, "track_duration", lambda p: None)
    svc = MusicService([], player, None, force=False, files=["/m/a.mp3"], clock=clock)
    svc.loop_mode = "playlist"
    svc._deadline = clock() + 1000

    svc._start_music()

    assert wait_until(lambda: svc.status == "done")
    assert player.played == ["a.mp3"]


def test_force_service_loops_until_stopped_regardless_of_the_deadline(monkeypatch):
    clock = FakeClock()
    lengths = {"a.mp3": 100.0, "b.mp3": 100.0}
    player = TimedPlayer(clock, lengths)
    monkeypatch.setattr(music_main, "track_duration", lambda p: lengths[os.path.basename(p)])
    svc = MusicService([], player, None, force=True, files=["/m/a.mp3", "/m/b.mp3"], clock=clock)

    def no_break():
        raise AssertionError("a force szolgaltatas nem nezi a csengetesi rendet")

    svc.get_break = no_break
    svc.loop_mode = "playlist"
    player.hook = lambda n: svc.stop() if n == 7 else None

    svc()

    assert wait_until(lambda: svc.status == "stopped")
    assert player.played == ["a.mp3", "b.mp3"] * 3 + ["a.mp3"]


def test_force_track_loop_uses_the_gapless_player_flag(monkeypatch):
    svc, player, clock = _bounded(monkeypatch, ["/m/a.mp3"], {"a.mp3": 10.0}, 5, "track")
    svc.force = True

    svc()

    assert wait_until(lambda: svc.status == "done")
    assert player.played == ["a.mp3"]
    assert player.loop_at_play == [True]


def test_no_deadline_loops_like_force(monkeypatch):
    svc, player, clock = _bounded(
        monkeypatch, ["/m/a.mp3", "/m/b.mp3"], {"a.mp3": 600.0, "b.mp3": 600.0}, None, "playlist"
    )
    player.hook = lambda n: svc.stop() if n == 7 else None

    assert svc() is True

    assert wait_until(lambda: svc.status == "stopped")
    assert player.played == ["a.mp3", "b.mp3"] * 3 + ["a.mp3"]


def test_no_deadline_track_loop_uses_the_gapless_player_flag(monkeypatch):
    svc, player, clock = _bounded(monkeypatch, ["/m/a.mp3"], {"a.mp3": 10.0}, None, "track")

    svc()

    assert wait_until(lambda: svc.status == "done")
    assert player.loop_at_play == [True]


def test_set_loop_on_a_bounded_service_never_enables_the_gapless_flag(monkeypatch):
    svc, player, clock = _bounded(monkeypatch, ["/m/a.mp3"], {"a.mp3": 10.0}, 100, "off")
    svc._deadline = clock() + 100
    svc.status = "playing"

    svc.set_loop("track")

    assert svc.loop_mode == "track"
    assert player.loop is False


def test_set_loop_on_an_unbounded_playing_service_enables_the_gapless_flag():
    player = FakePlayer()
    svc = _svc(["/m/a.mp3"], player)  # force=True: nincs hatar
    svc.status = "playing"

    svc.set_loop("track")

    assert player.loop is True


def test_non_force_service_that_does_not_fit_is_skipped(monkeypatch):
    svc, player, clock = _bounded(
        monkeypatch, ["/m/a.mp3", "/m/b.mp3"], {"a.mp3": 10.0, "b.mp3": 10.0}, 15, "playlist"
    )

    assert svc() is False
    assert player.played == []


def test_session_start_without_inheriting_the_loop_mode():
    session = MusicSession()
    session.set_loop("playlist")
    player = FakePlayer()
    svc = _svc(["/m/a.mp3"], player)

    assert session.start(svc, inherit_loop=False) is True

    assert svc.loop_mode == "off"
    assert wait_until(lambda: svc.status == "done")
    assert player.played == ["a.mp3"]
    assert player.loop_at_play == [False]
    assert session.loop_mode == "playlist"  # a munkamenet beallitasa megmarad


# ---------- M3: a lejatszo szal kozben irja az indexet / a szamot: egyszeri olvasas ----------


class _FlippingIndex(MusicService):
    """Az index az elso olvasasra 0, utana None (mintha kozben leallt volna a lejatszas)."""

    reads = 0

    @property
    def index(self):
        self.reads += 1
        return 0 if self.reads == 1 else None

    @index.setter
    def index(self, value):
        pass


class _FlippingCurrent(MusicService):
    """A current az elso olvasasra egy fajl, utana None."""

    reads = 0

    @property
    def current(self):
        self.reads += 1
        return "/m/a.mp3" if self.reads == 1 else None

    @current.setter
    def current(self, value):
        pass


@pytest.mark.parametrize("mode", ["off", "track", "playlist"])
def test_next_track_reads_the_index_once(mode):
    svc = _FlippingIndex([], FakePlayer(), None, force=True, files=["/m/a.mp3", "/m/b.mp3"])
    svc.loop_mode = mode

    nxt = svc.next_track  # a regi kod masodik olvasaskor None + 1-et szamolt: TypeError

    assert nxt == {"off": "/m/b.mp3", "track": "/m/a.mp3", "playlist": "/m/b.mp3"}[mode]
    assert svc.reads == 1


def test_next_track_with_an_index_past_the_queue_is_none():
    svc = _svc(["/m/a.mp3"])
    svc.index = 5

    assert svc.next_track is None


def test_session_status_reads_current_once():
    svc = _FlippingCurrent([], FakePlayer(), None, force=True, files=["/m/a.mp3", "/m/b.mp3"])
    svc.status = "playing"
    session = MusicSession()
    session._current = svc

    status = session.status()  # a regi kod basename(None)-t hivott: TypeError -> 500

    assert status["current"] == "a.mp3"
    assert svc.reads == 1
