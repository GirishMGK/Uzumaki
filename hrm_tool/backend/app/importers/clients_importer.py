import uuid

from sqlmodel import Session

from app.importers.base import ImportResult, one_of_label, required, run_validators
from app.importers.friendly_values import (
    CLIENT_STATUS_MAP,
    ENGAGEMENT_TYPE_MAP,
    ENTITY_TYPE_MAP,
    FIRMS,
    NATURE_MAP,
    PARTNERS,
    PRIORITY_MAP,
    YES_NO_MAP,
    lookup,
)
from app.importers.resolvers import resolve_group_id, resolve_partner_id

VALIDATORS = [
    required("client_code"),
    required("name"),
    one_of_label("entity_type", ENTITY_TYPE_MAP.keys()),
    one_of_label("nature", NATURE_MAP.keys()),
    one_of_label("engagement_type", ENGAGEMENT_TYPE_MAP.keys(), required_field=False),
    one_of_label("status", CLIENT_STATUS_MAP.keys(), required_field=False),
    one_of_label("partner_responsible", PARTNERS),
    one_of_label("priority", PRIORITY_MAP.keys(), required_field=False),
    one_of_label("mnc_status", YES_NO_MAP.keys(), required_field=False),
    one_of_label("firm", FIRMS, required_field=False),
]


def validate_client_rows(rows: list[dict]) -> ImportResult:
    return run_validators(rows, VALIDATORS)


def row_to_client_kwargs(row: dict, db: Session, actor_id: uuid.UUID) -> dict:
    """Turns one validated row into Client(**kwargs). Needs `db` (and the
    importing user's id) because Partner responsible and Group both
    resolve to records that get created on first use, same as the Add-one
    form's behaviour.
    """
    partner_id = resolve_partner_id(db, row["partner_responsible"].strip(), actor_id)
    group_id = resolve_group_id(db, row.get("group") or "", actor_id)
    return dict(
        client_code=row["client_code"].strip(),
        name=row["name"].strip(),
        is_listed=bool(lookup(ENTITY_TYPE_MAP, row["entity_type"])),
        entity_class=lookup(NATURE_MAP, row["nature"]),
        primary_service_type=lookup(ENGAGEMENT_TYPE_MAP, row.get("engagement_type") or "") if row.get("engagement_type") else None,
        relationship_status=lookup(CLIENT_STATUS_MAP, row.get("status") or "Active") or "ACTIVE",
        relationship_partner_id=partner_id,
        priority=lookup(PRIORITY_MAP, row.get("priority") or "Medium") or "MEDIUM",
        is_mnc=bool(lookup(YES_NO_MAP, row.get("mnc_status") or "No")),
        group_id=group_id,
        practicing_firm=lookup({f: f for f in FIRMS}, row.get("firm") or "") if row.get("firm") else None,
        sector=row.get("nature_of_business") or None,
    )
