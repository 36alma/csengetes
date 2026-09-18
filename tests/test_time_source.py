import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from time_source import TimeSource


def test_manual_mode_advances_with_elapsed_time():
    ts = TimeSource()
    fixed = datetime(2026, 1, 1, 12, 0, 0)
    ts.set_manual(fixed)

    now1 = ts.get_now()
    assert now1 >= fixed

    import time

    time.sleep(1.1)
    now2 = ts.get_now()
    assert now2 > now1
    assert (now2 - fixed) >= timedelta(seconds=1)


def test_default_mode_is_ntp():
    ts = TimeSource()
    assert ts.get_mode() == "ntp"


def test_get_now_falls_back_to_system_clock_without_sync():
    ts = TimeSource()
    now = ts.get_now()
    assert abs((now - datetime.now()).total_seconds()) < 5
