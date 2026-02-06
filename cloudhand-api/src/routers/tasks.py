from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from database.connection import get_db
from database.models import Deployment

router = APIRouter()

class MaintenanceTask(BaseModel):
    id: str
    title: str
    description: str
    severity: str
    status: str
    type: str

@router.get("/", response_model=List[MaintenanceTask])
async def get_tasks(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Deployment).options(selectinload(Deployment.application)).order_by(Deployment.created_at.desc())
    )
    deployments = result.scalars().all()

    tasks: list[MaintenanceTask] = []
    for dep in deployments:
        app_name = dep.application.name if dep.application else "application"
        status = dep.status or "pending"
        if status in {"deploying", "pending"}:
            ui_status = "in-progress"
            severity = "medium"
        elif status == "running":
            ui_status = "completed"
            severity = "low"
        else:
            ui_status = "pending"
            severity = "high"

        tasks.append(
            MaintenanceTask(
                id=str(dep.id),
                title=f"{app_name} deployment",
                description=f"Deployment status: {status}",
                severity=severity,
                status=ui_status,
                type="deployment",
            )
        )

    return tasks
