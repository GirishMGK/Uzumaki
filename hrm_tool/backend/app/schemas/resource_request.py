import uuid

from pydantic import BaseModel


class ResourceRequestCreate(BaseModel):
    engagement_id: uuid.UUID
    required_grade: str | None = None
    required_skills: list | None = None
    required_office: uuid.UUID | None = None
    date_from: str
    date_to: str
    headcount: int = 1
    priority: str | None = None
    sla_due_on: str | None = None


class ResourceRequestFulfil(BaseModel):
    allocation_id: uuid.UUID


class ResourceRequestRead(BaseModel):
    id: uuid.UUID
    engagement_id: uuid.UUID
    requested_by: uuid.UUID | None
    required_grade: str | None
    required_skills: list | None
    required_office: uuid.UUID | None
    date_from: str
    date_to: str
    headcount: int
    status: str
    fulfilment_allocation_ids: list | None
    priority: str | None
    requested_on: str | None
    sla_due_on: str | None
    is_active: bool
