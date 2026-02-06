from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from database.models import User
from routers.deps import get_current_user
from services import onboarding as onboarding_service

router = APIRouter(tags=["onboarding"])


class ProviderSetupRequest(BaseModel):
    provider: str = Field(..., description="Provider id e.g. hetzner")
    token: str = Field(..., description="API token for the provider")
    project: Optional[str] = Field(None, description="Optional project override")


@router.get("/status")
async def status(current_user: User = Depends(get_current_user)):
    return onboarding_service.onboarding_status()


@router.post("/provider")
async def connect_provider(
    body: ProviderSetupRequest, current_user: User = Depends(get_current_user)
):
    try:
        result = onboarding_service.configure_provider(
            provider=body.provider, token=body.token, project=body.project
        )
        return {"status": "connected", **result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/scan")
async def refresh_scan(current_user: User = Depends(get_current_user)):
    status = onboarding_service.onboarding_status()
    provider = status.get("provider")
    if not provider:
        raise HTTPException(status_code=400, detail="Provider not configured")

    config = onboarding_service.load_provider_config(provider)
    token = config.get("token")
    if not token:
        raise HTTPException(status_code=400, detail="No provider token stored")

    graph = onboarding_service.run_scan(provider, token)
    return {
        "status": "ok",
        "provider": provider,
        "resources": len(graph.nodes),
    }
