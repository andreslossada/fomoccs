"""SQLAdmin setup for the FomoCCS admin panel."""

from fastapi import FastAPI
from sqladmin import Admin
from starlette.middleware.sessions import SessionMiddleware

from api.admin.auth import AdminAuth
from api.admin.views import ALL_VIEWS
from api.config import get_settings
from api.database import engine


def setup_admin(app: FastAPI) -> Admin:
    """Wire up SQLAdmin with authentication and all model views.

    Must be called BEFORE any catch-all static-files mount so that
    ``/admin`` takes routing priority.
    """
    settings = get_settings()
    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)
    authentication_backend = AdminAuth(secret_key=settings.secret_key)

    admin = Admin(
        app,
        engine,
        title="FomoCCS Admin",
        authentication_backend=authentication_backend,
    )

    for view_class in ALL_VIEWS:
        admin.add_view(view_class)

    return admin
