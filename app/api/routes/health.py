"""Website health route."""

from fastapi import APIRouter, Request

from app.services import health as service

router = APIRouter()


@router.get("/api/health")
def health(request: Request) -> dict:
    return service.health(request.app.state.router)
