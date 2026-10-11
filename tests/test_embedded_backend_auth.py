"""The in-process backends Uzumaki embeds must not trust whoever can reach
their localhost port -- only the Uzumaki page that started them.

Loan Analytics runs in trust-host-auth mode (no second login), so the gate
is a per-launch token in the iframe URL; its own password login is off.
"""
from __future__ import annotations

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOANS_BACKEND = os.path.join(REPO_ROOT, "loans_tool", "backend")
for p in (REPO_ROOT, LOANS_BACKEND):
    if p not in sys.path:
        sys.path.insert(0, p)

TOKEN = "per-launch-test-token"


@pytest.fixture()
def loans_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from fcmr_core.config import settings

    for attr, sub in [
        ("data_dir", ""), ("uploads_dir", "uploads"), ("parquet_dir", "parquet"),
        ("outputs_dir", "outputs"), ("logs_dir", "logs"), ("backups_dir", "backups"),
    ]:
        monkeypatch.setattr(settings, attr, tmp_path / sub)
    monkeypatch.setattr(settings, "catalog_path", tmp_path / "catalog.duckdb")
    monkeypatch.setenv("LOANS_TRUST_HOST_AUTH", "1")
    monkeypatch.setenv("LOANS_HOST_TOKEN", TOKEN)

    from loan_app.main import app

    with TestClient(app, follow_redirects=False) as client:
        yield client


def test_request_without_token_is_refused(loans_client):
    assert loans_client.get("/").status_code == 403


def test_request_with_wrong_token_is_refused(loans_client):
    assert loans_client.get("/", params={"hub_token": "guess"}).status_code == 403


def test_correct_token_creates_a_session_that_persists(loans_client):
    first = loans_client.get("/", params={"hub_token": TOKEN})
    assert first.status_code != 403
    # later navigation inside the iframe doesn't repeat the token
    assert loans_client.get("/").status_code != 403


def test_password_login_is_disabled_in_trust_host_mode(loans_client):
    resp = loans_client.post("/login", data={"username": "admin", "password": "admin123"})
    assert resp.status_code == 403


def test_login_page_stays_reachable_as_readiness_probe(loans_client):
    assert loans_client.get("/login").status_code == 200


def test_pick_free_port_returns_a_bindable_port():
    import socket

    from common.net import pick_free_port

    port = pick_free_port()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", port))


@pytest.mark.parametrize("path", ["_pages/hrm.py", "_pages/loans.py"])
def test_embedded_pages_use_no_fixed_port(path):
    src = open(os.path.join(REPO_ROOT, path), encoding="utf-8").read()
    assert "pick_free_port(" in src
    assert "PORT = 87" not in src


def test_no_well_known_default_passwords_seeded():
    for rel in [
        "hrm_tool/backend/app/jobs/startup_seed.py",
        "loans_tool/backend/loan_app/api/auth.py",
    ]:
        src = open(os.path.join(REPO_ROOT, rel), encoding="utf-8").read()
        assert "ChangeMe!2026" not in src and "admin123" not in src, rel
