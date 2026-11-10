import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.remote_mic.jitter import JitterBuffer


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def block(value):
    return np.full(1152, value, dtype=np.float32)


def make():
    clock = Clock()
    return JitterBuffer(clock=clock), clock


def feed_until_ready(buf):
    """Hány blokk kellett az első kiolvasható blokkig (az előtöltés mérete)."""
    fed = 0
    while True:
        buf.feed(block(1.0))
        fed += 1
        if buf.read_block() is not None:
            return fed


def drain(buf):
    """Kiolvas mindent; az utolsó, üres olvasás alulcsordulásnak számít."""
    values = []
    while True:
        item = buf.read_block()
        if item is None:
            return values
        values.append(float(item[0]))


def test_prefills_three_blocks_then_keeps_order():
    buf, _ = make()
    assert buf.read_block() is None
    buf.feed(block(1.0))
    buf.feed(block(2.0))
    assert buf.read_block() is None
    buf.feed(block(3.0))
    assert drain(buf) == [1.0, 2.0, 3.0]


def test_each_underrun_raises_prefill_up_to_the_cap():
    buf, _ = make()
    needed = []
    for _ in range(8):
        needed.append(feed_until_ready(buf))
        drain(buf)
    assert needed == [3, 4, 5, 6, 7, 8, 8, 8]


def test_prefill_drops_by_one_after_a_clean_minute():
    buf, clock = make()
    feed_until_ready(buf)
    drain(buf)  # a célszint 4
    for _ in range(5):
        buf.feed(block(1.0))
    clock.now += 60.0
    assert buf.read_block() is not None  # a célszint visszaáll 3-ra
    drain(buf)  # alulcsordulás: 4
    assert feed_until_ready(buf) == 4


def test_prefill_stays_up_before_the_clean_minute_ends():
    buf, clock = make()
    feed_until_ready(buf)
    drain(buf)  # a célszint 4
    for _ in range(5):
        buf.feed(block(1.0))
    clock.now += 59.0
    assert buf.read_block() is not None
    drain(buf)  # alulcsordulás: 5
    assert feed_until_ready(buf) == 5


def test_prefill_never_goes_below_three():
    buf, clock = make()
    for _ in range(5):
        buf.feed(block(1.0))
    clock.now += 600.0
    assert buf.read_block() is not None
    drain(buf)  # alulcsordulás: 3 + 1
    assert feed_until_ready(buf) == 4


def test_sustained_excess_drops_exactly_one_oldest_block():
    buf, clock = make()
    for value in (1.0, 2.0, 3.0, 4.0, 5.0):
        buf.feed(block(value))  # 5 blokk = célszint + 2
    clock.now = 4.9
    buf.feed(block(6.0))
    clock.now = 5.0
    buf.feed(block(7.0))
    assert drain(buf) == [2.0, 3.0, 4.0, 5.0, 6.0, 7.0]


def test_second_drop_needs_another_hold_period():
    buf, clock = make()
    for value in (1.0, 2.0, 3.0, 4.0, 5.0):
        buf.feed(block(value))
    clock.now = 5.0
    buf.feed(block(6.0))  # egy eldobás
    clock.now = 9.9
    buf.feed(block(7.0))  # még nincs újabb
    assert drain(buf) == [2.0, 3.0, 4.0, 5.0, 6.0, 7.0]


def test_interrupted_excess_restarts_the_hold_period():
    buf, clock = make()
    for value in (1.0, 2.0, 3.0, 4.0, 5.0):
        buf.feed(block(value))
    buf.read_block()
    buf.read_block()
    clock.now = 3.0
    buf.feed(block(6.0))  # 4 blokk: nincs a küszöb fölött, az időzítő törlődik
    clock.now = 6.0
    buf.feed(block(7.0))  # 5 blokk: az időzítő most indul
    assert drain(buf) == [3.0, 4.0, 5.0, 6.0, 7.0]


def test_hard_limit_keeps_the_newest_sixteen():
    buf, _ = make()
    for value in range(1, 21):
        buf.feed(block(float(value)))
    assert drain(buf) == [float(v) for v in range(5, 21)]


def test_clear_empties_and_restores_the_initial_prefill():
    buf, _ = make()
    feed_until_ready(buf)
    drain(buf)  # a célszint 4
    buf.feed(block(9.0))
    buf.clear()
    assert buf.read_block() is None
    assert feed_until_ready(buf) == 3
