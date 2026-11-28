"""Website health route."""

from fastapi import APIRouter, Request, Depends

from app.services import health as service

from app.security.permissions import require

router = APIRouter()


@router.get("/api/health", dependencies=[Depends(require("public"))])
def health(request: Request) -> dict:
    return service.health(request.app.state.router)
