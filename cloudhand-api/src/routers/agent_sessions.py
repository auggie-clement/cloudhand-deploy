from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from typing import List
from uuid import UUID
from datetime import datetime

from database.models import AgentSession as SessionModel, AgentMessage as MessageModel, Application
from schemas import AgentSession, AgentSessionCreate, AgentMessage, AgentMessageCreate
from routers.deps import get_db, get_current_user

router = APIRouter()

@router.get("/applications/{app_id}/sessions", response_model=List[AgentSession])
async def list_sessions(
    app_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """List all agent sessions for an application"""
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
        select(SessionModel)
        .where(SessionModel.application_id == app_id)
        .options(selectinload(SessionModel.messages))
        .order_by(SessionModel.last_activity.desc())
    )
    sessions = result.scalars().all()
    
    return sessions

@router.get("/sessions/{session_id}", response_model=AgentSession)
async def get_session(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Get a specific agent session with messages"""
    result = await db.execute(
        select(SessionModel)
        .where(SessionModel.id == session_id)
        .options(selectinload(SessionModel.messages))
    )
    session = result.scalars().first()
    
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Verify ownership
    # 1. Direct user ownership
    if session.user_id == current_user.id:
        return session
        
    # 2. App ownership (legacy/fallback)
    if session.application_id:
        result = await db.execute(
            select(Application).where(
                Application.id == session.application_id,
                Application.user_id == current_user.id
            )
        )
        app = result.scalars().first()
        if app:
            return session
            
    raise HTTPException(status_code=403, detail="Forbidden")

@router.post("/sessions", response_model=AgentSession)
async def create_session(
    session: AgentSessionCreate,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Create a new agent session"""
    
    # If application_id is provided, verify it exists and belongs to user
    if session.application_id:
        result = await db.execute(
            select(Application).where(
                Application.id == session.application_id,
                Application.user_id == current_user.id
            )
        )
        app = result.scalars().first()
        
        if not app:
            raise HTTPException(status_code=404, detail="Application not found")
    
    db_session = SessionModel(
        application_id=session.application_id,
        user_id=current_user.id,
        title=session.title,
        status=session.status,
        primary_run_id=session.primary_run_id,
        created_from_session_id=session.created_from_session_id,
        last_activity=datetime.utcnow()
    )
    
    db.add(db_session)
    await db.commit()
    await db.refresh(db_session)
    
    # Eagerly load messages to avoid lazy loading during response serialization
    result = await db.execute(
        select(SessionModel)
        .where(SessionModel.id == db_session.id)
        .options(selectinload(SessionModel.messages))
    )
    db_session = result.scalars().first()
    
    return db_session

@router.post("/sessions/{session_id}/messages", response_model=AgentMessage)
async def add_message(
    session_id: UUID,
    message: AgentMessageCreate,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Add a message to an agent session"""
    result = await db.execute(
        select(SessionModel).where(SessionModel.id == session_id)
    )
    session = result.scalars().first()
    
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Verify ownership
    is_owner = False
    if session.user_id == current_user.id:
        is_owner = True
    elif session.application_id:
        result = await db.execute(
            select(Application).where(
                Application.id == session.application_id,
                Application.user_id == current_user.id
            )
        )
        app = result.scalars().first()
        if app:
            is_owner = True
            
    if not is_owner:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    db_message = MessageModel(
        session_id=session_id,
        role=message.role,
        content=message.content,
        type=message.type,
        metadata_=message.metadata,
        timestamp=datetime.utcnow()
    )
    
    db.add(db_message)
    
    # Update session last_activity
    session.last_activity = datetime.utcnow()
    
    await db.commit()
    await db.refresh(db_message)
    
    return db_message

@router.get("/sessions", response_model=List[AgentSession])
async def list_all_sessions(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """List all agent sessions for the current user"""
    # Query sessions where user_id matches OR linked application is owned by user
    
    result = await db.execute(
        select(SessionModel)
        .outerjoin(Application)
        .where(
            (SessionModel.user_id == current_user.id) | 
            (Application.user_id == current_user.id)
        )
        .options(selectinload(SessionModel.messages))
        .order_by(SessionModel.last_activity.desc())
    )
    sessions = result.scalars().all()
    
    return sessions
