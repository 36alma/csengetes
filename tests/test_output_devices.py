import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import services.mixer.output as output


def test_list_output_devices_filters_host_api_outputs_and_duplicates(monkeypatch):
    devices = [
        {"name": "Mikrofon", "max_output_channels": 0, "hostapi": 0},
        {"name": "Hangszoro", "max_output_channels": 2, "hostapi": 0},
        {"name": "Hangszoro", "max_output_channels": 2, "hostapi": 0},  # duplikatum
        {"name": "Hangszoro", "max_output_channels": 2, "hostapi": 1},  # masik host API
        {"name": "HDMI", "max_output_channels": 2, "hostapi": 0},
    ]
    fake_sd = SimpleNamespace(query_devices=lambda: devices, default=SimpleNamespace(hostapi=0))
    monkeypatch.setattr(output, "sd", fake_sd)
    assert output.list_output_devices() == [(1, "Hangszoro"), (4, "HDMI")]
