from typing import List, Dict
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from services.secrets import SecretsService
from routers.deps import get_current_user

router = APIRouter()

class SecretCreate(BaseModel):
    name: str
    value: str

class SecretList(BaseModel):
    secrets: List[str]

def get_secrets_service():
    return SecretsService()

@router.get("/projects/{project_id}/secrets", response_model=SecretList)
async def list_secrets(
    project_id: str,
    service: SecretsService = Depends(get_secrets_service),
    user = Depends(get_current_user)
):
    secrets = service.list_secrets(project_id)
    return SecretList(secrets=secrets)

@router.post("/projects/{project_id}/secrets")
async def create_secret(
    project_id: str,
    secret: SecretCreate,
    service: SecretsService = Depends(get_secrets_service),
    user = Depends(get_current_user)
):
    # For now, we store simple key-value pairs where the key is 'value'
    # This can be expanded to support structured secrets later
    service.set_secret(project_id, secret.name, {"value": secret.value})
    return {"status": "ok"}

@router.delete("/projects/{project_id}/secrets/{name}")
async def delete_secret(
    project_id: str,
    name: str,
    service: SecretsService = Depends(get_secrets_service),
    user = Depends(get_current_user)
):
    service.delete_secret(project_id, name)
    return {"status": "ok"}
