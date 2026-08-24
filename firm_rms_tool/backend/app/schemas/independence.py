import uuid

from pydantic import BaseModel


class IndependenceDeclarationCreate(BaseModel):
    staff_id: uuid.UUID
    client_id: uuid.UUID
    declaration_fy: str
    has_financial_interest: bool = False
    has_relative_in_client: bool = False
    held_employment_last_2yrs: bool = False
    has_loan_from_client: bool = False
    other_threat_description: str | None = None


class IndependenceDeclarationReview(BaseModel):
    is_conflicted: bool
    notes: str | None = None


class IndependenceDeclarationRead(BaseModel):
    id: uuid.UUID
    staff_id: uuid.UUID
    client_id: uuid.UUID
    declaration_fy: str
    has_financial_interest: bool
    has_relative_in_client: bool
    held_employment_last_2yrs: bool
    has_loan_from_client: bool
    other_threat_description: str | None
    is_conflicted: bool
    declared_on: str | None
    reviewed_by: uuid.UUID | None
    is_active: bool
