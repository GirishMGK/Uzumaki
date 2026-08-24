"""Import every model module so SQLModel.metadata is fully populated.

Alembic's env.py and the dev `create_all()` bootstrap both import this
module rather than individual model files, so table/relationship
registration order never bites us.
"""
from sqlmodel import SQLModel

from app.models import allocation  # noqa: F401
from app.models import audit_log  # noqa: F401
from app.models import capacity  # noqa: F401
from app.models import client  # noqa: F401
from app.models import engagement  # noqa: F401
from app.models import reference  # noqa: F401
from app.models import scenario  # noqa: F401
from app.models import staff  # noqa: F401
from app.models import user  # noqa: F401

metadata = SQLModel.metadata
