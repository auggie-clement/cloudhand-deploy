from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from uuid import UUID
from datetime import datetime

from database.models import Operation as OperationModel, Application
from schemas import Operation, OperationCreate
from routers.deps import get_db, get_current_user

router = APIRouter()

@router.get("/applications/{app_id}/operations", response_model=List[Operation])
async def list_operations(
    app_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """List all operations for an application"""
    result = await db.execute(
        select(Application).where(
            Application.id == app_id,
            Application.user_id == current_user.id
        )
    )
    app = result.scalars().first()
    
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    
    result = await db.execute(
        select(OperationModel)
        .where(OperationModel.application_id == app_id)
        .order_by(OperationModel.started_at.desc())
    )
    operations = result.scalars().all()
    
    return operations

@router.get("/operations/{operation_id}", response_model=Operation)
async def get_operation(
    operation_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Get a specific operation"""
    result = await db.execute(
        select(OperationModel).where(OperationModel.id == operation_id)
    )
    operation = result.scalars().first()
    
    if not operation:
        raise HTTPException(status_code=404, detail="Operation not found")
    
    # Verify user owns the application
    result = await db.execute(
        select(Application).where(
            Application.id == operation.application_id,
            Application.user_id == current_user.id
        )
    )
    app = result.scalars().first()
    
    if not app:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    return operation

@router.post("/operations", response_model=Operation)
async def create_operation(
    operation: OperationCreate,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Create a new operation"""
    # Verify user owns the application
    result = await db.execute(
        select(Application).where(
            Application.id == operation.application_id,
            Application.user_id == current_user.id
        )
    )
    app = result.scalars().first()
    
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    
    db_operation = OperationModel(
        application_id=operation.application_id,
        type=operation.type,
        status=operation.status,
        trigger=operation.trigger,
        phases=operation.phases,
        changeset=operation.changeset,
        sandbox_id=operation.sandbox_id,
        session_id=operation.session_id,
        started_at=datetime.utcnow()
    )
    
    db.add(db_operation)
    await db.commit()
    await db.refresh(db_operation)
    
    return db_operation

@router.get("/operations", response_model=List[Operation])
async def list_all_operations(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """List all operations for the current user"""
    result = await db.execute(
        select(OperationModel)
        .join(Application)
        .where(Application.user_id == current_user.id)
        .order_by(OperationModel.started_at.desc())
    )
    operations = result.scalars().all()
    
    return operations
