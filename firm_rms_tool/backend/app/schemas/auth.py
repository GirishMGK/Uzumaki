import re
import uuid

from pydantic import BaseModel, field_validator

from app.models.enums import UserRole

# Deliberately not pydantic's EmailStr: email-validator rejects "special-use
# or reserved" TLDs (.local, .test, .internal, ...) by default, which is
# exactly what an on-premise firm's internal mail domain often looks like
# (§0.4 "on-premise deployable"). Login only needs syntactic validity here —
# deliverability isn't the concern for an internal auth identifier.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _validate_email_shape(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError("value is not a valid email address")
        return v


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class CurrentUserResponse(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: UserRole
    staff_id: uuid.UUID | None = None
