"""Model layer: what is stored, and how it is read and written.

``base`` holds the engine, the session factory and the role constants; ``user``
and ``device`` hold one table each together with its queries.

Import the modules, not their contents — ``user.get`` and ``device.get`` are
meant to read as what they are, and a bare ``get`` would not:

    from app.models import device, user
    record = await user.get(session, user_id)
"""

from app.models.base import (  # noqa: F401
    ROLE_ADMIN,
    ROLE_OPERATOR,
    VALID_ROLES,
    Base,
    SessionLocal,
    close_db,
    engine,
    get_session,
    init_db,
)
from app.models.device import Device  # noqa: F401
from app.models.user import User  # noqa: F401
