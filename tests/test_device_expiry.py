import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.devices import DeviceStore
from services.devices.audit import log_expiring


class Clock:
    now = 1_000_000

    def __call__(self):
        return self.now


def test_log_expiring_warns_for_soon_and_expired(tmp_path, caplog):
    clock = Clock()
    store = DeviceStore(path=str(tmp_path / "devices.json"), token_days=90, clock=clock)
    store.create("Regi", "192.168.1.50")
    clock.now += 80 * 86400
    store.create("Uj", "192.168.1.51")
    off, _ = store.create("Ki", "192.168.1.52")
    store.update(off.id, enabled=False)
    clock.now += 20 * 86400  # a "Regi" lejart, "Uj" 70 nap mulva jar le
    with caplog.at_level(logging.INFO, logger="csengetes"):
        assert log_expiring(store) == 1
    text = "\n".join(r.getMessage() for r in caplog.records if " expiring" in r.getMessage())
    assert "[device:Regi] expiring days=0" in text
    assert "[device:Uj]" not in text and "[device:Ki]" not in text
