"""HTTP surface. Routers are thin: validate, authenticate, delegate to services."""

from fastapi import APIRouter

from app.api import backups, credentials, db_users, instances, teams

api_router = APIRouter()
api_router.include_router(teams.router)
api_router.include_router(instances.router)
api_router.include_router(credentials.router)
api_router.include_router(db_users.router)
api_router.include_router(backups.router)

__all__ = ["api_router"]
