import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server


def test_index_without_token_is_rejected():
    client = server.app.test_client()
    resp = client.get("/")
    assert resp.status_code == 403


def test_index_with_wrong_token_is_rejected():
    client = server.app.test_client()
    resp = client.get("/?token=wrong")
    assert resp.status_code == 403


def test_static_asset_without_session_is_rejected():
    client = server.app.test_client()
    resp = client.get("/app.js")
    assert resp.status_code == 403


def test_api_without_session_is_rejected():
    client = server.app.test_client()
    resp = client.get("/api/schedules")
    assert resp.status_code == 403


def test_index_with_correct_token_grants_session_for_later_requests():
    client = server.app.test_client()
    resp = client.get(f"/?token={server.AUTH_TOKEN}")
    assert resp.status_code == 200

    # a kovetkezo kerelmek mar a munkamenet-sutit hasznaljak, token nelkul
    api_resp = client.get("/api/schedules")
    assert api_resp.status_code == 200

    asset_resp = client.get("/app.js")
    assert asset_resp.status_code == 200
