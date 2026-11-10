import json
import os
import socket
import sys

import pytest
import sounddevice

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import micserver.__main__ as cli
from micserver.capture import MicCapture
from remote_mic_helpers import read_state, write_state

GOOD = "A" * 43


class FakeStream:
    """A sounddevice.RawInputStream felülete."""

    last = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls = []
        FakeStream.last = self

    def start(self):
        self.calls.append("start")

    def stop(self):
        self.calls.append("stop")

    def close(self):
        self.calls.append("close")


@pytest.fixture
def stream(monkeypatch):
    monkeypatch.setattr(sounddevice, "RawInputStream", FakeStream)
    return FakeStream


def test_capture_opens_the_format_the_mixer_expects(stream):
    capture = MicCapture(lambda block: None, device=3)
    capture.start()
    kwargs = stream.last.kwargs
    assert (kwargs["samplerate"], kwargs["blocksize"], kwargs["channels"], kwargs["dtype"], kwargs["device"]) == (44100, 1152, 1, "int16", 3)
    assert stream.last.calls == ["start"]


def test_capture_hands_over_an_independent_copy_of_each_block(stream):
    received = []
    capture = MicCapture(received.append)
    capture.start()
    native = bytearray(b"\x01\x02" * 1152)
    stream.last.kwargs["callback"](native, 1152, None, None)
    native[0] = 0  # a PortAudio puffere a callback után újrafelhasználódik
    assert received == [b"\x01\x02" * 1152] and isinstance(received[0], bytes)


def test_capture_stop_closes_the_stream_once(stream):
    capture = MicCapture(lambda block: None)
    capture.start()
    opened = stream.last
    capture.stop()
    capture.stop()
    assert opened.calls == ["start", "stop", "close"]


def _paths(tmp_path):
    return str(tmp_path / "state.json"), str(tmp_path / "config.json")


def test_set_token_stores_the_entered_value(tmp_path, monkeypatch):
    state, config = _paths(tmp_path)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: f"  {GOOD} \n")
    assert cli.main(["--set-token", "--state", state, "--config", config]) == 0
    assert read_state(state) == {"current": GOOD, "previous": None}


def test_set_token_replaces_both_old_values(tmp_path, monkeypatch):
    state, config = _paths(tmp_path)
    write_state(state, "B" * 43, "C" * 43)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: GOOD)
    assert cli.main(["--set-token", "--state", state, "--config", config]) == 0
    assert read_state(state) == {"current": GOOD, "previous": None}


def test_set_token_rejects_malformed_input_and_keeps_the_old(tmp_path, monkeypatch, capsys):
    state, config = _paths(tmp_path)
    write_state(state, "B" * 43)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "rövid")
    assert cli.main(["--set-token", "--state", state, "--config", config]) == 2
    assert read_state(state) == {"current": "B" * 43, "previous": None}
    assert "rövid" not in capsys.readouterr().out


def test_refuses_to_serve_without_a_token(tmp_path, capsys):
    state, config = _paths(tmp_path)
    assert cli.main(["--state", state, "--config", config]) == 2
    assert "--set-token" in capsys.readouterr().out


def test_busy_port_is_reported_without_a_traceback(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_configure_logging", lambda: None)  # a gyökérnaplót a többi tesztnek hagyjuk
    state, config = _paths(tmp_path)
    write_state(state, GOOD)
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        with open(config, "w", encoding="utf-8") as fh:
            json.dump({"host": "127.0.0.1", "port": blocker.getsockname()[1]}, fh)
        assert cli.main(["--state", state, "--config", config]) == 1
    assert "port" in capsys.readouterr().out


def test_config_defaults_when_missing(tmp_path):
    assert cli.load_config(str(tmp_path / "nincs.json")) == {
        "name": "Mikrofongép", "host": "0.0.0.0", "port": 8770, "input_device": None,
    }


def test_config_values_override_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"name": "Igazgatói", "port": 9000, "input_device": 2}), encoding="utf-8")
    assert cli.load_config(str(path)) == {"name": "Igazgatói", "host": "0.0.0.0", "port": 9000, "input_device": 2}


@pytest.mark.parametrize("content", [
    "nem json",
    "[1]",
    '{"name": ""}',
    '{"name": 5}',
    '{"port": 0}',
    '{"port": "8770"}',
    '{"port": true}',
    '{"input_device": "mic"}',
])
def test_invalid_config_is_refused(tmp_path, content):
    path = tmp_path / "config.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        cli.load_config(str(path))
