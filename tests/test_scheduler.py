import os
import sys
from datetime import datetime
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scheduler import Scheduler


def _make(now):
    ts = MagicMock()
    ts.get_now.return_value = now
    player = MagicMock()
    sched = Scheduler(ts, player)
    sched.set_active_schedule({"events": [{"time": "19:42", "file": "bell.mp3"}]})
    return sched, ts, player


def test_bell_fires_only_once_per_minute_even_if_schedule_reactivated():
    sched, _ts, player = _make(datetime(2026, 9, 24, 19, 42, 0))
    with patch("scheduler.os.path.isfile", return_value=True):
        sched._tick()
        sched.set_active_schedule({"events": [{"time": "19:42", "file": "bell.mp3"}]})
        sched._tick()
        sched._tick()
    assert player.play.call_count == 1


def test_bell_fires_again_next_day():
    sched, ts, player = _make(datetime(2026, 9, 24, 19, 42, 0))
    with patch("scheduler.os.path.isfile", return_value=True):
        sched._tick()
        ts.get_now.return_value = datetime(2026, 9, 25, 19, 42, 0)
        sched._tick()
    assert player.play.call_count == 2
