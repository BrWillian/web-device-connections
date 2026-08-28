"""Entrypoint. The application itself is assembled in ``app/main.py``.

Kept as a thin re-export so the uvicorn target is the same shape as the relay's
(`uvicorn main:app`), and so the package can be imported without a module named
after the process.
"""

from app.main import app
