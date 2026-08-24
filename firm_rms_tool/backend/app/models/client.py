"""Client and client_group masters."""
import uuid

from sqlalchemy import Column
from sqlalchemy.types import JSON
from sqlmodel import Field

from app.models.base import TimestampSoftDeleteMixin, UUIDPKMixin
from app.models.enums import AcceptanceStatus, EntityClass, RelationshipStatus, RiskRating


class ClientGroup(UUIDPKMixin, TimestampSoftDeleteMixin, table=True):
    __tablename__ = "client_groups"

    group_code: str = Field(index=True, unique=True, nullable=False)
    group_name: str = Field(nullable=False)
    group_relationship_partner_id: uuid.UUID | None = Field(default=None, foreign_key="staff.id")
    notes: str | None = Field(default=None)


class Client(UUIDPKMixin, TimestampSoftDeleteMixin, table=True):
    __tablename__ = "clients"

    client_code: str = Field(index=True, unique=True, nullable=False)
    name: str = Field(nullable=False)
    legal_name: str | None = Field(default=None)
    cin: str | None = Field(default=None)
    pan: str | None = Field(default=None)
    group_id: uuid.UUID | None = Field(default=None, foreign_key="client_groups.id", index=True)
    industry_code: str | None = Field(default=None)
    sector: str | None = Field(default=None)
    entity_class: EntityClass = Field(nullable=False)
    is_pie: bool = Field(default=False)
    is_component: bool = Field(default=False)
    parent_client_id: uuid.UUID | None = Field(default=None, foreign_key="clients.id")
    registered_office_city: str | None = Field(default=None)
    operating_locations: list | None = Field(default=None, sa_column=Column(JSON))
    fy_end: str = Field(default="03-31")
    relationship_status: RelationshipStatus = Field(default=RelationshipStatus.PROSPECT)
    client_since: str | None = Field(default=None)
    exit_date: str | None = Field(default=None)
    exit_reason: str | None = Field(default=None)
    risk_rating: RiskRating = Field(default=RiskRating.MEDIUM)
    acceptance_status: AcceptanceStatus = Field(default=AcceptanceStatus.PENDING)
    acceptance_date: str | None = Field(default=None)
    continuance_due_date: str | None = Field(default=None)
    relationship_partner_id: uuid.UUID | None = Field(default=None, foreign_key="staff.id")
