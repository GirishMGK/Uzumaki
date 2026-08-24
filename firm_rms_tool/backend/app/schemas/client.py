import uuid

from pydantic import BaseModel

from app.models.enums import AcceptanceStatus, EntityClass, RelationshipStatus, RiskRating


class ClientBase(BaseModel):
    client_code: str
    name: str
    legal_name: str | None = None
    cin: str | None = None
    pan: str | None = None
    group_id: uuid.UUID | None = None
    industry_code: str | None = None
    sector: str | None = None
    entity_class: EntityClass
    is_pie: bool = False
    is_component: bool = False
    parent_client_id: uuid.UUID | None = None
    registered_office_city: str | None = None
    operating_locations: list[dict] | None = None
    fy_end: str = "03-31"
    relationship_status: RelationshipStatus = RelationshipStatus.PROSPECT
    client_since: str | None = None
    exit_date: str | None = None
    exit_reason: str | None = None
    risk_rating: RiskRating = RiskRating.MEDIUM
    acceptance_status: AcceptanceStatus = AcceptanceStatus.PENDING
    acceptance_date: str | None = None
    continuance_due_date: str | None = None
    relationship_partner_id: uuid.UUID | None = None


class ClientCreate(ClientBase):
    pass


class ClientRead(ClientBase):
    id: uuid.UUID
    is_active: bool
