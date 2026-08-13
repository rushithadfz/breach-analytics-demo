from fastapi import APIRouter

from app.api.v1 import routes_documents, routes_persons, routes_review, routes_runs

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(routes_documents.router)
api_v1_router.include_router(routes_persons.router)
api_v1_router.include_router(routes_runs.router)
api_v1_router.include_router(routes_review.router)
