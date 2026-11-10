"""A mikrofongép és a mixer közös protokollja (v1).

Csak a standard könyvtárat használja, mert a mixer is ezt a modult importálja.
A titok soha nem megy át a hálózaton: a felek HMAC-kel bizonyítják, hogy ismerik.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
from typing import Any

PROTOCOL_VERSION = 1
DEFAULT_PORT = 8770

SAMPLE_RATE = 44100
BLOCK_SAMPLES = 1152  # egyezik a mixer blokkjával, így nincs átmintavételezés
FRAME_BYTES = BLOCK_SAMPLES * 2
STREAM_INFO = {"codec": "pcm_s16le", "sample_rate": SAMPLE_RATE, "channels": 1, "frame_samples": BLOCK_SAMPLES}

CLOSE_POLICY = 1008  # sikertelen azonosítás
CLOSE_INTERNAL = 1011  # a gépen nem nyitható meg a mikrofon
CLOSE_BUSY = 1013  # a gépet már használja egy mixer

_NONCE_RE = re.compile(r"[0-9a-f]{32}")
_CREDENTIAL_RE = re.compile(r"[A-Za-z0-9_-]{43}")


class ProtocolError(Exception):
    """Formailag hibás üzenet."""


def new_nonce() -> str:
    return secrets.token_hex(16)


def new_credential() -> str:
    return secrets.token_urlsafe(32)


def is_valid_nonce(value: object) -> bool:
    return isinstance(value, str) and bool(_NONCE_RE.fullmatch(value))


def is_valid_credential(value: object) -> bool:
    return isinstance(value, str) and bool(_CREDENTIAL_RE.fullmatch(value))


def _mac(key: str, message: str) -> bytes:
    return hmac.new(key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()


def client_proof(credential: str, server_nonce: str, client_nonce: str) -> str:
    return _mac(credential, f"c|{server_nonce}|{client_nonce}").hex()


def server_proof(credential: str, server_nonce: str, client_nonce: str) -> str:
    return _mac(credential, f"s|{server_nonce}|{client_nonce}").hex()


def rotated_proof(new_value: str, rotate_nonce: str) -> str:
    return _mac(new_value, f"r|{rotate_nonce}").hex()


def derive_rotated(credential: str, rotate_nonce: str) -> str:
    """Az új titok a régiből és a nyilvános nonce-ból, így ő sem megy át a hálózaton."""
    digest = _mac(credential, f"rotate|{rotate_nonce}")
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def proofs_equal(received: object, expected: str) -> bool:
    """Időzítésbiztos összehasonlítás; a hálózatról jövő érték bármi lehet."""
    if not isinstance(received, str):
        return False
    return hmac.compare_digest(received.encode("utf-8"), expected.encode("utf-8"))


def encode(message: dict) -> str:
    return json.dumps(message, separators=(",", ":"), ensure_ascii=False)


def decode(raw: object) -> dict:
    if not isinstance(raw, str):
        raise ProtocolError("szöveges üzenet kell")
    try:
        message = json.loads(raw)
    except ValueError as exc:
        raise ProtocolError("hibás JSON") from exc
    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
        raise ProtocolError("hiányzó üzenettípus")
    return message


def atomic_write_json(path: str, data: Any, prefix: str = ".state-") -> None:
    """Tartós, atomikus írás: áramszünet után vagy a régi, vagy az új tartalom marad."""
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=prefix, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
