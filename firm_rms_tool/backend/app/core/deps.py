"""FastAPI dependencies: DB session, current-user resolution, RBAC guards."""
import uuid
from collections.abc import Generator

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlmodel import Session

from app.core.security import decode_token
from app.db.session import get_session
from app.models.enums import FINANCIALLY_PRIVILEGED_ROLES, UserRole
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


def get_db() -> Generator[Session, None, None]:
    yield from get_session()


def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if token is None:
        raise credentials_error
    try:
        payload = decode_token(token)
    except jwt.PyJWTError:
        raise credentials_error
    if payload.get("type") != "access":
        raise credentials_error
    user_id = payload.get("sub")
    if user_id is None:
        raise credentials_error
    try:
        user = db.get(User, uuid.UUID(user_id))
    except ValueError:
        raise credentials_error
    if user is None or not user.is_active:
        raise credentials_error
    return user


def require_roles(*allowed: UserRole):
    """Route dependency: 403 unless the caller's role is in `allowed`.

    This is layer 1 of the 3-layer RBAC enforcement (route dependency ->
    service-level object check -> SQL row filter) described in §2.
    """

    def _check(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role {user.role} is not permitted to perform this action",
            )
        return user

    return _check


def can_see_financials(user: User) -> bool:
    return user.role in FINANCIALLY_PRIVILEGED_ROLES


def get_client_ip(request: Request) -> str | None:
    if request.client:
        return request.client.host
    return None
