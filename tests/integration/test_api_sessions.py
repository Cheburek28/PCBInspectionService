from __future__ import annotations

import uuid

from tests.integration.conftest import Api


def test_auth_is_required(api: Api) -> None:
    r = api.client.get(f"/api/v1/sessions/{uuid.uuid4()}")
    assert r.status_code == 401
    assert r.headers["content-type"] == "application/problem+json"
    assert r.headers["WWW-Authenticate"] == "Bearer"
    body = r.json()
    assert body["code"] == "UNAUTHORIZED"
    assert body["type"].endswith("#unauthorized")
    assert body["request_id"] == r.headers["X-Request-ID"]


def test_malformed_and_wrong_keys(api: Api) -> None:
    for header in ("Bearer nonsense", "Bearer pcbis_abc_def", "Basic xyz"):
        r = api.client.get(f"/api/v1/sessions/{uuid.uuid4()}", headers={"Authorization": header})
        assert r.status_code == 401


def test_request_id_is_echoed(api: Api) -> None:
    r = api.request("GET", "/health/live", headers={"X-Request-ID": "abc-123"})
    assert r.headers["X-Request-ID"] == "abc-123"


def test_create_get_close(api: Api) -> None:
    s = api.create_session(product_name="Demo", operator="op1", client_meta={"app": "test"})
    assert s["status"] == "open"
    assert s["station"] == "test-station"
    assert s["active_references"] == {}
    assert s["client_meta"] == {"app": "test"}

    got = api.request("GET", f"/api/v1/sessions/{s['id']}").json()
    assert got["id"] == s["id"]

    closed = api.request("POST", f"/api/v1/sessions/{s['id']}/close").json()
    assert closed["status"] == "closed"
    assert closed["closed_at"] is not None
    again = api.request("POST", f"/api/v1/sessions/{s['id']}/close").json()
    assert again["closed_at"] == closed["closed_at"]


def test_unknown_session(api: Api) -> None:
    r = api.request("GET", f"/api/v1/sessions/{uuid.uuid4()}")
    assert r.status_code == 404
    assert r.json()["code"] == "NOT_FOUND"


def test_validation_error_is_a_problem(api: Api) -> None:
    r = api.request("POST", "/api/v1/sessions", json={"product_code": "", "unknown": 1})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert {tuple(e["loc"]) for e in body["errors"]} >= {("body", "product_code"), ("body", "unknown")}


def test_unknown_route_is_a_problem(api: Api) -> None:
    r = api.request("GET", "/api/v1/nope")
    assert r.status_code == 404
    assert r.headers["content-type"] == "application/problem+json"
