"""Authentication and role enforcement."""

import pytest

from fsmes.services import Invalid, auth


def test_password_hashing_roundtrip():
    encoded = auth.hash_password("correct horse")
    assert auth.verify_password("correct horse", encoded)
    assert not auth.verify_password("wrong horse", encoded)
    assert not auth.verify_password("anything", None)
    assert "correct horse" not in encoded  # never stored in the clear


def test_tokens_are_signed_and_expire(session):
    person = auth.authenticate(session, "SCOTT", "operator")
    token = auth.issue_token(person, "secret-a", ttl_seconds=60)

    assert auth.read_token(token, "secret-a")["sub"] == "SCOTT"
    assert auth.read_token(token, "different-secret") is None  # forged/other instance
    assert auth.read_token(token[:-2] + "xx", "secret-a") is None  # tampered
    assert auth.read_token(auth.issue_token(person, "secret-a", ttl_seconds=-1), "secret-a") is None  # expired


def test_authenticate_rejects_bad_credentials(session):
    with pytest.raises(Invalid):
        auth.authenticate(session, "SCOTT", "not-the-password")
    with pytest.raises(Invalid):
        auth.authenticate(session, "NOBODY", "operator")


def test_protected_endpoints_require_sign_in(anon):
    assert anon.get("/workorders").status_code == 401
    assert anon.post("/workorders", json={"code": "X", "material": "FG-COLA", "quantity": 1}).status_code == 401
    assert anon.get("/dashboard/summary").status_code == 401


def test_health_and_metrics_stay_public(anon):
    assert anon.get("/health").status_code == 200
    assert anon.get("/metrics").status_code == 200


def test_login_sets_cookie_so_the_dashboard_works(anon):
    response = anon.post("/auth/login", json={"code": "SCOTT", "password": "operator"})
    assert response.status_code == 200
    assert "mes_session" in response.cookies
    assert anon.get("/dashboard/summary").status_code == 200  # cookie alone is enough


def test_bad_login_is_rejected(anon):
    assert anon.post("/auth/login", json={"code": "SCOTT", "password": "nope"}).status_code == 400


def test_roles_are_enforced(client, admin, supervisor):
    # operator may run production but not change master data
    assert client.post("/workorders", json={"code": "WO-R1", "material": "FG-COLA", "quantity": 5}).status_code == 201
    assert client.post("/masterdata/materials", json={"code": "M1", "name": "New"}).status_code == 403
    assert client.get("/audit").status_code == 403  # supervisor-only

    # supervisor may read the audit trail; admin may edit master data
    assert supervisor.get("/audit").status_code == 200
    assert admin.post("/masterdata/materials", json={"code": "M1", "name": "New"}).status_code == 201


def test_audit_records_the_signed_in_user(client, supervisor):
    client.post("/workorders", json={"code": "WO-WHO", "material": "FG-COLA", "quantity": 2})
    entries = supervisor.get("/audit", params={"entity_id": "WO-WHO"}).json()
    assert entries and all(entry["actor"] == "SCOTT" for entry in entries)


# ------------------------------------------------- behind a reverse proxy
# The MES does not terminate TLS. A plant puts a proxy in front of it, and
# these are the two things that has to get right.


def test_the_session_cookie_is_https_only_when_the_request_arrived_over_https(anon):
    """A session cookie without `Secure` is sent back over plain HTTP too, so
    one stray http:// link inside the plant hands somebody a live session. The
    flag follows the request's scheme, which behind a proxy is the scheme the
    proxy reports."""
    response = anon.post("https://testserver/auth/login", json={"code": "SCOTT", "password": "operator"})

    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]


def test_the_cookie_is_not_https_only_on_a_laptop(anon):
    """A Secure cookie on http://127.0.0.1:8000 is a cookie the browser drops,
    and the dashboard stops signing in. The zero-setup laptop path must keep
    working."""
    response = anon.post("http://testserver/auth/login", json={"code": "SCOTT", "password": "operator"})

    assert response.status_code == 200
    assert "Secure" not in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]


def test_the_server_reads_forwarded_headers_from_a_proxy_on_this_machine():
    """`fsmes run-api` is uvicorn, and the operate page tells a plant that
    `X-Forwarded-Proto` is honoured from a proxy on localhost and has to be
    allowed explicitly for a proxy anywhere else. That claim is uvicorn's
    default, not this project's code, so it is asserted rather than trusted."""
    import uvicorn

    config = uvicorn.Config("fsmes.api.app:create_app", factory=True)

    assert config.proxy_headers is True
    assert config.forwarded_allow_ips == ["127.0.0.1"] or config.forwarded_allow_ips == "127.0.0.1"
