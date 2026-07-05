"""HTTP surface. Routers are thin: validate, authenticate, delegate to services."""

from fastapi import APIRouter

from app.api import audit, backups, credentials, db_users, instances, principals, teams

api_router = APIRouter()
api_router.include_router(teams.router)
api_router.include_router(principals.router)
api_router.include_router(instances.router)
api_router.include_router(credentials.router)
api_router.include_router(db_users.router)
api_router.include_router(backups.router)
api_router.include_router(audit.router)

__all__ = ["api_router"]
