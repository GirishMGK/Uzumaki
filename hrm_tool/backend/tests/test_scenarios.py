"""API coverage for /scenarios — what-if planning + impact check + promote (P10)."""
from app.models.enums import UserRole
from tests.conftest import auth_headers, make_user
from tests.factories import make_client, make_department, make_engagement, make_staff


def _setup(session):
    dept = make_department(session)
    cl = make_client(session)
    engagement = make_engagement(session, cl.id, dept.id)
    staff = make_staff(session)
    return dept, cl, engagement, staff


def _headers(client, session, email="scn1@x.com"):
    make_user(session, UserRole.RESOURCE_MANAGER, email=email)
    return auth_headers(client, email)


def test_create_scenario_add_line_and_clean_impact(client, session):
    _, _, engagement, staff = _setup(session)
    headers = _headers(client, session)

    scenario = client.post(
        "/api/v1/scenarios", headers=headers,
        json={"name": "Q3 what-if", "date_from": "2026-09-01", "date_to": "2026-09-30"},
    ).json()
    assert scenario["status"] == "DRAFT"

    line = client.post(
        f"/api/v1/scenarios/{scenario['id']}/lines", headers=headers,
        json={
            "staff_id": str(staff.id), "engagement_id": str(engagement.id), "role_on_engagement": "TEAM_MEMBER",
            "date_from": "2026-09-01", "date_to": "2026-09-10", "allocation_pct": 50,
        },
    )
    assert line.status_code == 201

    impact = client.get(f"/api/v1/scenarios/{scenario['id']}/impact", headers=headers)
    assert impact.status_code == 200
    body = impact.json()
    assert body["has_blocking"] is False
    assert len(body["lines"]) == 1
    # a freshly-created engagement defaults to PIPELINE status, which is a
    # legitimate INFO-severity violation (R22) — INFO never blocks promotion.
    assert not any(v["severity"] in ("BLOCK", "WARN") for v in body["lines"][0]["violations"])
    staff_row = next(s for s in body["staff_impact"] if s["staff_id"] == str(staff.id))
    assert staff_row["added_pct"] > 0


def test_scenario_flags_intra_scenario_overallocation(client, session):
    _, _, engagement, staff = _setup(session)
    headers = _headers(client, session, "scn2@x.com")

    scenario = client.post(
        "/api/v1/scenarios", headers=headers,
        json={"name": "Overbooked what-if", "date_from": "2026-09-01", "date_to": "2026-09-30"},
    ).json()
    client.post(
        f"/api/v1/scenarios/{scenario['id']}/lines", headers=headers,
        json={
            "staff_id": str(staff.id), "engagement_id": str(engagement.id), "role_on_engagement": "TEAM_MEMBER",
            "date_from": "2026-09-01", "date_to": "2026-09-15", "allocation_pct": 70,
        },
    )
    client.post(
        f"/api/v1/scenarios/{scenario['id']}/lines", headers=headers,
        json={
            "staff_id": str(staff.id), "engagement_id": str(engagement.id), "role_on_engagement": "TEAM_MEMBER",
            "date_from": "2026-09-05", "date_to": "2026-09-10", "allocation_pct": 60,
        },
    )
    impact = client.get(f"/api/v1/scenarios/{scenario['id']}/impact", headers=headers).json()
    assert impact["has_blocking"] is True
    codes = {v["code"] for ln in impact["lines"] for v in ln["violations"]}
    assert "SCENARIO_OVERALLOCATION" in codes


def test_promote_writes_clean_lines_and_skips_blocked_ones(client, session):
    _, _, engagement, staff = _setup(session)
    headers = _headers(client, session, "scn3@x.com")

    scenario = client.post(
        "/api/v1/scenarios", headers=headers,
        json={"name": "Promote test", "date_from": "2026-09-01", "date_to": "2026-09-30"},
    ).json()
    client.post(
        f"/api/v1/scenarios/{scenario['id']}/lines", headers=headers,
        json={
            "staff_id": str(staff.id), "engagement_id": str(engagement.id), "role_on_engagement": "TEAM_MEMBER",
            "date_from": "2026-09-01", "date_to": "2026-09-15", "allocation_pct": 70,
        },
    )
    client.post(
        f"/api/v1/scenarios/{scenario['id']}/lines", headers=headers,
        json={
            "staff_id": str(staff.id), "engagement_id": str(engagement.id), "role_on_engagement": "TEAM_MEMBER",
            "date_from": "2026-09-05", "date_to": "2026-09-10", "allocation_pct": 60,
        },
    )
    promoted = client.post(f"/api/v1/scenarios/{scenario['id']}/promote", headers=headers)
    assert promoted.status_code == 200
    body = promoted.json()
    assert body["promoted_count"] == 0
    assert len(body["skipped"]) == 2

    real_allocs = client.get("/api/v1/allocations", headers=headers, params={"staff_id": str(staff.id)}).json()
    assert real_allocs == []

    scenario_after = client.get(f"/api/v1/scenarios/{scenario['id']}", headers=headers).json()
    assert scenario_after["status"] == "PROMOTED"

    # can't add lines or re-promote once promoted
    add_after = client.post(
        f"/api/v1/scenarios/{scenario['id']}/lines", headers=headers,
        json={
            "staff_id": str(staff.id), "engagement_id": str(engagement.id), "role_on_engagement": "TEAM_MEMBER",
            "date_from": "2026-09-20", "date_to": "2026-09-21", "allocation_pct": 10,
        },
    )
    assert add_after.status_code == 422
    repromote = client.post(f"/api/v1/scenarios/{scenario['id']}/promote", headers=headers)
    assert repromote.status_code == 422


def test_promote_clean_line_creates_real_draft_allocation(client, session):
    _, _, engagement, staff = _setup(session)
    headers = _headers(client, session, "scn4@x.com")

    scenario = client.post(
        "/api/v1/scenarios", headers=headers,
        json={"name": "Clean promote", "date_from": "2026-09-01", "date_to": "2026-09-30"},
    ).json()
    client.post(
        f"/api/v1/scenarios/{scenario['id']}/lines", headers=headers,
        json={
            "staff_id": str(staff.id), "engagement_id": str(engagement.id), "role_on_engagement": "TEAM_MEMBER",
            "date_from": "2026-09-01", "date_to": "2026-09-10", "allocation_pct": 50,
        },
    )
    promoted = client.post(f"/api/v1/scenarios/{scenario['id']}/promote", headers=headers)
    body = promoted.json()
    assert body["promoted_count"] == 1
    assert body["skipped"] == []

    real_allocs = client.get("/api/v1/allocations", headers=headers, params={"staff_id": str(staff.id)}).json()
    assert len(real_allocs) == 1
    assert real_allocs[0]["status"] == "DRAFT"
    assert real_allocs[0]["date_from"] == "2026-09-01"


def test_discard_scenario_soft_deletes(client, session):
    headers = _headers(client, session, "scn5@x.com")
    scenario = client.post(
        "/api/v1/scenarios", headers=headers,
        json={"name": "Discard me", "date_from": "2026-09-01", "date_to": "2026-09-30"},
    ).json()
    resp = client.delete(f"/api/v1/scenarios/{scenario['id']}", headers=headers)
    assert resp.status_code == 200
    listed = client.get("/api/v1/scenarios", headers=headers).json()
    assert scenario["id"] not in [s["id"] for s in listed]
