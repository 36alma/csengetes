import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from model import InputDevice
from services.mixer.mixing import BLOCK_SIZE, SAMPLE_RATE


class FakeMic:
    """Mic helyettes hardver nelkul: allando szinuszt ad."""

    def __init__(self):
        self.volume = 1.0
        self.current_output_device = InputDevice(index=0, name="fake")
        t = np.arange(BLOCK_SIZE) / SAMPLE_RATE
        self._block = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

    def listinput(self):
        return [InputDevice(index=0, name="fake"), InputDevice(index=3, name="masik")]

    def setoutputdevices(self, device):
        self.current_output_device = device

    def start(self, samplerate, blocksize):
        pass

    def stop(self):
        pass

    def read_block(self):
        return self._block


def wait_until(predicate, timeout=3.0):
    """Var, amig a feltetel igaz nem lesz; a vegen is visszaadja az erteket."""
    deadline = time.time() + timeout
    while not predicate() and time.time() < deadline:
        time.sleep(0.02)
    return predicate()
