import uuid

from pydantic import BaseModel

from app.models.enums import SkillCategory


class OfficeCreate(BaseModel):
    code: str
    name: str
    city: str
    state: str
    is_head_office: bool = False
    address: str | None = None
    timezone: str = "Asia/Kolkata"


class OfficeRead(OfficeCreate):
    id: uuid.UUID
    is_active: bool


class DepartmentCreate(BaseModel):
    code: str
    name: str
    head_staff_id: uuid.UUID | None = None


class DepartmentRead(DepartmentCreate):
    id: uuid.UUID
    is_active: bool


class SkillCreate(BaseModel):
    code: str
    name: str
    category: SkillCategory
    parent_skill_id: uuid.UUID | None = None


class SkillRead(SkillCreate):
    id: uuid.UUID
    is_active: bool


class ClientGroupCreate(BaseModel):
    group_code: str
    group_name: str
    group_relationship_partner_id: uuid.UUID | None = None
    notes: str | None = None


class ClientGroupRead(ClientGroupCreate):
    id: uuid.UUID
    is_active: bool
