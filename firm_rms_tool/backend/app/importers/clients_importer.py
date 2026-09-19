from app.importers.base import ImportResult, one_of, required, run_validators
from app.models.enums import EntityClass, RelationshipStatus, RiskRating

VALIDATORS = [
    required("client_code"),
    required("name"),
    one_of("entity_class", {e.value for e in EntityClass}),
    one_of("relationship_status", {e.value for e in RelationshipStatus}, required_field=False),
    one_of("risk_rating", {e.value for e in RiskRating}, required_field=False),
]


def validate_client_rows(rows: list[dict]) -> ImportResult:
    return run_validators(rows, VALIDATORS)


def row_to_client_kwargs(row: dict) -> dict:
    return dict(
        client_code=row["client_code"].strip(),
        name=row["name"].strip(),
        legal_name=row.get("legal_name") or None,
        entity_class=row["entity_class"].strip(),
        relationship_status=(row.get("relationship_status") or RelationshipStatus.PROSPECT.value).strip(),
        risk_rating=(row.get("risk_rating") or RiskRating.MEDIUM.value).strip(),
        sector=row.get("sector") or None,
        is_pie=str(row.get("is_pie", "")).strip().upper() in ("TRUE", "1", "YES"),
    )
