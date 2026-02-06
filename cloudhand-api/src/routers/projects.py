from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List
import uuid

from database.connection import get_db
from database.models import Project, User
from schemas import ProjectCreate, ProjectRead # Need to create these schemas
from routers.deps import get_current_user

router = APIRouter(prefix="/projects", tags=["projects"])

@router.post("/", response_model=ProjectRead)
async def create_project(
    project_in: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Check if project already exists for this repo?
    # For now, just create.
    
    project = Project(
        id=uuid.uuid4(),
        user_id=current_user.id,
        github_owner=project_in.github_owner,
        github_repo=project_in.github_repo,
        default_branch=project_in.default_branch,
        github_installation_id=project_in.github_installation_id,
        infra_branch=project_in.infra_branch or "cloudhand/infra",
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return project

@router.get("/", response_model=List[ProjectRead])
async def list_projects(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Project).where(Project.user_id == current_user.id))
    return result.scalars().all()

@router.get("/{project_id}", response_model=ProjectRead)
async def get_project(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Project).where(Project.id == project_id, Project.user_id == current_user.id))
    project = result.scalars().first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project
