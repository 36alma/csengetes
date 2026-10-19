"""Az eszkoz-belepes donto logikaja: HTTP-t nem ismer, csak ertekeket kap."""

from __future__ import annotations

from typing import NamedTuple

from model import Device

from .store import DeviceStore, normalize_ip


class Decision(NamedTuple):
    allowed: bool
    reason: str  # "ok" | "bad_token" | "disabled" | "expired" | "ip_mismatch"
    device: Device | None


def parse_bearer(header: str | None) -> str | None:
    """Az 'Authorization: Bearer <token>' fejlecbol a token; a seman kisbetu is jo."""
    if not header:
        return None
    parts = header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1]


def check(store: DeviceStore, authorization: str | None, client_ip: str | None) -> Decision:
    token = parse_bearer(authorization)
    device = store.find_by_token(token) if token else None
    if device is None:
        return Decision(False, "bad_token", None)
    if not device.enabled:
        return Decision(False, "disabled", device)
    if not store.is_current(device.id, device.token_hash):
        return Decision(False, "expired", device)  # engedett es a hash friss, tehat csak a lejarat maradt
    if normalize_ip(client_ip) != device.ip_address:
        return Decision(False, "ip_mismatch", device)
    return Decision(True, "ok", device)
