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
