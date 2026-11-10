import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from micserver import protocol


NS = "a" * 32
NC = "b" * 32
TOKEN = "t" * 43


def test_client_and_server_proofs_differ_for_the_same_input():
    # különben a gép a mixer saját válaszát visszaküldve "bizonyíthatna"
    assert protocol.client_proof(TOKEN, NS, NC) != protocol.server_proof(TOKEN, NS, NC)


def test_proof_depends_on_token_and_both_nonces():
    base = protocol.client_proof(TOKEN, NS, NC)
    assert protocol.client_proof("x" * 43, NS, NC) != base
    assert protocol.client_proof(TOKEN, "c" * 32, NC) != base
    assert protocol.client_proof(TOKEN, NS, "c" * 32) != base


def test_proofs_equal_accepts_match_and_rejects_everything_else():
    proof = protocol.client_proof(TOKEN, NS, NC)
    assert protocol.proofs_equal(proof, proof)
    assert not protocol.proofs_equal(proof, protocol.server_proof(TOKEN, NS, NC))
    assert not protocol.proofs_equal(None, proof)
    assert not protocol.proofs_equal(123, proof)
    assert not protocol.proofs_equal("ékezet", proof)


def test_derive_is_deterministic_and_token_shaped():
    derived = protocol.derive_rotated(TOKEN, NC)
    assert derived == protocol.derive_rotated(TOKEN, NC)
    assert derived != TOKEN
    assert protocol.is_valid_credential(derived)


def test_derive_depends_on_token_and_nonce():
    derived = protocol.derive_rotated(TOKEN, NC)
    assert protocol.derive_rotated("x" * 43, NC) != derived
    assert protocol.derive_rotated(TOKEN, NS) != derived


def test_new_credential_is_valid_and_unique():
    first, second = protocol.new_credential(), protocol.new_credential()
    assert protocol.is_valid_credential(first) and first != second


@pytest.mark.parametrize("bad", [None, 5, "", "rövid", "!" * 43, "a" * 44, "a" * 43 + "\n"])
def test_invalid_credentials(bad):
    assert not protocol.is_valid_credential(bad)


def test_nonce_format():
    assert protocol.is_valid_nonce(protocol.new_nonce())
    for bad in (None, 7, "", "A" * 32, "a" * 31, "a|" * 16, "a" * 32 + "\n"):
        assert not protocol.is_valid_nonce(bad)


def test_message_roundtrip():
    message = {"type": "hello", "v": 1, "name": "Igazgatói"}
    assert protocol.decode(protocol.encode(message)) == message


@pytest.mark.parametrize("raw", [b"binary", "nem json", "[1]", '{"v":1}', '{"type":5}'])
def test_decode_rejects_malformed(raw):
    with pytest.raises(protocol.ProtocolError):
        protocol.decode(raw)


def test_frame_size_matches_block():
    assert protocol.FRAME_BYTES == 2304
    assert protocol.STREAM_INFO == {"codec": "pcm_s16le", "sample_rate": 44100, "channels": 1, "frame_samples": 1152}


def test_atomic_write_replaces_file(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("régi", encoding="utf-8")
    protocol.atomic_write_json(str(path), {"current": "ő"})
    assert json.loads(path.read_text(encoding="utf-8")) == {"current": "ő"}
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_atomic_write_keeps_old_file_on_failure(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"current": "regi"}', encoding="utf-8")
    with pytest.raises(TypeError):
        protocol.atomic_write_json(str(path), {"bad": object()})
    assert json.loads(path.read_text(encoding="utf-8")) == {"current": "regi"}
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_atomic_write_syncs_before_replace(tmp_path, monkeypatch):
    calls = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(protocol.os, "fsync", lambda fd: (calls.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(protocol.os, "replace", lambda a, b: (calls.append("replace"), real_replace(a, b))[1])
    protocol.atomic_write_json(str(tmp_path / "state.json"), {})
    assert calls == ["fsync", "replace"]


def test_importing_protocol_does_not_pull_audio_or_network_deps():
    # a mixer ezt a modult importálja: a csomag többi része ne töltődjön be vele
    code = (
        "import sys; sys.path.insert(0, r'{}');"
        "import micserver.protocol as p;"
        "assert 'websockets' not in sys.modules and 'sounddevice' not in sys.modules"
    ).format(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import subprocess

    subprocess.run([sys.executable, "-c", code], check=True)
