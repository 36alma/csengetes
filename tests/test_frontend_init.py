"""Az app.js inditasa node alatt, egy minimalis DOM-helyettesben (ha nincs node, kihagyva)."""

import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = os.environ.get("CSENGETES_APP_JS") or os.path.join(ROOT, "static", "app.js")  # felulirhato (regi valtozat ellenorzese)
HARNESS = os.path.join(ROOT, "tests", "js", "app_init_harness.js")
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node nincs telepitve")


def _run(tmp_path, responses):
    path = tmp_path / "responses.json"
    path.write_text(json.dumps(responses), encoding="utf-8")
    out = subprocess.run(
        [NODE, HARNESS, APP_JS, str(path)], capture_output=True, text=True, timeout=30, encoding="utf-8"
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


MIXER = {
    "running": True, "status": 0, "flags": [], "level": 0, "levels": {}, "mic_live": False, "output_ok": True,
    "volume": {"master": 1, "music": 1, "mic": 1, "bell": 1}, "music": {"status": "idle"},
}


def _strip(strip_id, state="off", live=False):
    return {"id": strip_id, "name": "Igazgatói", "host": "10.0.0.5", "live": live, "state": state, "volume": 0.5, "level": 0.1}


def _mixer_with(*strips):
    return {**MIXER, "remote_mics": list(strips)}


def _remote_loader_errors(result):
    # a többi betöltő a mock üres válaszaira hibázhat, azokat a saját tesztjeik nézik
    return [line for line in result["loaderErrors"] if "loadRemoteMics" in line]


def test_mixer_builds_one_strip_per_machine(tmp_path):
    result = _run(tmp_path, {"/api/mixer": _mixer_with(_strip("rm_1"), _strip("rm_2", "connecting", True))})
    assert result["connection"] == "Szerver: kapcsolódva", result["loaderErrors"]
    assert result["strips"] == ["rm_1", "rm_2"]


def test_mixer_without_machines_has_no_strips(tmp_path):
    result = _run(tmp_path, {"/api/mixer": MIXER})
    assert result["connection"] == "Szerver: kapcsolódva"
    assert result["strips"] == []


def test_unchanged_machine_list_keeps_the_strips_while_polling(tmp_path):
    first = _mixer_with(_strip("rm_1"), _strip("rm_2"))
    second = _mixer_with(_strip("rm_1", "on_air", True), _strip("rm_2"))
    result = _run(tmp_path, {"/api/mixer": first, "__second__": {"/api/mixer": second}})
    assert result["strips"] == ["rm_1", "rm_2"] and result["stripInserts"] == 2


def test_changed_machine_list_rebuilds_the_strips(tmp_path):
    first = _mixer_with(_strip("rm_1"), _strip("rm_2"))
    second = _mixer_with(_strip("rm_3"))
    result = _run(tmp_path, {"/api/mixer": first, "__second__": {"/api/mixer": second}})
    assert result["strips"] == ["rm_3"] and result["stripInserts"] == 3


def test_on_air_machine_lights_the_header_badge(tmp_path):
    assert _run(tmp_path, {"/api/mixer": _mixer_with(_strip("rm_1", "on_air", True))})["onAirHidden"] is False
    assert _run(tmp_path, {"/api/mixer": _mixer_with(_strip("rm_1"))})["onAirHidden"] is True


def test_init_loads_the_machine_table(tmp_path):
    source = {"id": "rm_1", "name": "Igazgatói", "host": "10.0.0.5", "port": 8770, "live": False, "state": "off",
              "volume": 1.0, "created_at": 1, "rotated_at": 1, "last_seen": None}
    result = _run(tmp_path, {"/api/remote-mics": {"sources": [source], "max": 8}})
    assert "/api/remote-mics" in result["fetched"]
    assert _remote_loader_errors(result) == [] and result["unhandled"] == []


def test_init_survives_an_empty_machine_response(tmp_path):
    result = _run(tmp_path, {"/api/remote-mics": None})
    assert "/api/remote-mics" in result["fetched"]
    assert _remote_loader_errors(result) == [] and result["unhandled"] == []


def test_app_js_syntax():
    subprocess.run([NODE, "--check", APP_JS], check=True, timeout=30)


def test_init_without_microphone_still_loads_everything(tmp_path):
    result = _run(tmp_path, {"/api/mixer/inputs": {"inputs": [], "current": None}})
    fetched = result["fetched"]
    for path in ("/api/media", "/api/schedules", "/api/time", "/api/output/devices", "/api/logs?limit=1000", "/api/devices"):
        assert path in fetched, f"{path} nem toltodott be: {fetched}"
    assert result["unhandled"] == []
    assert result["inputOptions"] == [{"text": "Nincs mikrofon", "disabled": True}]


def test_init_survives_failing_loader(tmp_path):
    # null valaszok (pl. halozati hiba) mellett is vegigfut az inditas
    result = _run(tmp_path, {"/api/mixer/inputs": {"inputs": [{"index": 1, "name": "m"}], "current": None},
                             "/api/music": None})
    assert "/api/logs?limit=1000" in result["fetched"]
    assert result["unhandled"] == []


def test_init_loads_devices_and_survives_odd_responses(tmp_path):
    result = _run(tmp_path, {"/api/devices": None, "/api/logs?limit=1000": None})
    assert "/api/devices" in result["fetched"]
    assert "/api/logs?limit=1000" in result["fetched"]
    assert result["unhandled"] == []


def test_init_renders_devices_with_buffer_and_esp_log(tmp_path):
    device = {"id": "d_1", "name": "Aula", "ip_address": "10.0.0.5", "status": "connected", "enabled": True,
              "token_expires_at": 9_999_999_999, "last_seen": 1,
              "buffer": {"fill_ms": 420, "capacity_ms": 1000, "underruns": 2, "drops": 0, "received_at": 1}}
    result = _run(tmp_path, {"/api/devices": {"devices": [device]},
                             "/api/devices/d_1/log?limit=500": {"events": [{"type": "drop", "ts": 1, "source": "esp"}]}})
    assert result["unhandled"] == []
    broken = _run(tmp_path, {"/api/devices": {"devices": [dict(device, buffer={"fill_ms": "x"})]}})
    assert broken["unhandled"] == []
