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


def test_app_js_syntax():
    subprocess.run([NODE, "--check", APP_JS], check=True, timeout=30)


def test_init_without_microphone_still_loads_everything(tmp_path):
    result = _run(tmp_path, {"/api/mixer/inputs": {"inputs": [], "current": None}})
    fetched = result["fetched"]
    for path in ("/api/media", "/api/schedules", "/api/time", "/api/output/devices", "/api/logs?limit=300"):
        assert path in fetched, f"{path} nem toltodott be: {fetched}"
    assert result["unhandled"] == []
    assert result["inputOptions"] == [{"text": "Nincs mikrofon", "disabled": True}]


def test_init_survives_failing_loader(tmp_path):
    # null valaszok (pl. halozati hiba) mellett is vegigfut az inditas
    result = _run(tmp_path, {"/api/mixer/inputs": {"inputs": [{"index": 1, "name": "m"}], "current": None},
                             "/api/music": None})
    assert "/api/logs?limit=300" in result["fetched"]
    assert result["unhandled"] == []
