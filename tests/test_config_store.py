import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config_store


def _fresh_config(tmp_path, monkeypatch, content=None):
    path = tmp_path / "config.json"
    if content is not None:
        path.write_text(json.dumps(content), encoding="utf-8")
    monkeypatch.setattr(config_store, "CONFIG_PATH", str(path))
    return config_store.Config(), path


def test_device_defaults(tmp_path, monkeypatch):
    cfg, _ = _fresh_config(tmp_path, monkeypatch)
    assert cfg.DEVICE_PORT == 8766
    assert cfg.DEVICE_TOKEN_DAYS == 90


def test_device_settings_roundtrip(tmp_path, monkeypatch):
    cfg, path = _fresh_config(tmp_path, monkeypatch, {"device_port": 9001, "device_token_days": 30})
    assert (cfg.DEVICE_PORT, cfg.DEVICE_TOKEN_DAYS) == (9001, 30)
    cfg.save_config()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["device_port"] == 9001 and saved["device_token_days"] == 30


def test_device_model_defaults():
    from model import Device

    d = Device(id="d_1", name="Aula", ip_address="10.0.0.5", token_hash="h", token_expires_at=2, created_at=1)
    assert d.enabled is True and d.last_seen is None and d.mac_address is None
