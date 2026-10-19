import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import services.mic.main as mic_main
from services.mic import Mic


@pytest.mark.parametrize("exc", [ValueError("No input device matching"), mic_main.sd.PortAudioError("no device")])
def test_mic_without_input_device_does_not_crash(monkeypatch, exc):
    def boom(*args, **kwargs):
        raise exc

    monkeypatch.setattr(mic_main.sd, "query_devices", boom)
    mic = Mic()
    assert mic.current_output_device is None
    with pytest.raises(RuntimeError):
        mic.start(44100, 1152)
