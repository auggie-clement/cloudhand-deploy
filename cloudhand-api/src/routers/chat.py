import os
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

class ChatMessage(BaseModel):
    message: str
    session_id: Optional[str] = None

from services.agent import AgentService
from fastapi.responses import StreamingResponse
from routers.deps import get_current_user
from database.models import User
from fastapi import Depends

@router.post("/")
async def chat(body: ChatMessage, user: User = Depends(get_current_user)):
    try:
        # Create a fresh agent service for each request to avoid history pollution
        # In a real app, we'd load history from DB based on session_id
        service = AgentService()
        return StreamingResponse(
            service.chat_stream(body.message, session_id=body.session_id, github_token=user.access_token),
            media_type="application/x-ndjson"
        )
    except Exception as e:
        print(f"Error in chat: {e}")
        raise HTTPException(status_code=500, detail=str(e))
