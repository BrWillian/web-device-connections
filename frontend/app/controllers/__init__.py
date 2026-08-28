"""Controller layer: one router per area of the app.

A controller reads the session, calls a service, and renders a view. It holds no
rules of its own — anything that would be true regardless of HTTP belongs in
``app/services``.
"""

from app.controllers import auth, dashboard, devices, users  # noqa: F401
