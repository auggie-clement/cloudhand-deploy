from __future__ import annotations

import uuid
import os
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from database.connection import get_db
from database.models import User

SESSION_COOKIE_NAME = "user_id"


def _expire_session_cookie() -> dict[str, str]:
    """Return headers that clear the session cookie on the client."""

    resp = Response()
    resp.delete_cookie(
        SESSION_COOKIE_NAME,
        httponly=True,
        samesite="lax",
        secure=False,
    )
    header_value = resp.headers.get("set-cookie")
    return {"Set-Cookie": header_value} if header_value else {}


async def get_current_user(
    request: Request, db: AsyncSession = Depends(get_db)
) -> User:
    """Resolve the current user from the session cookie."""

    # Optional API-key auth for headless usage.
    # If CLOUDHAND_API_KEY is set, requests may authenticate by sending either:
    #   - X-API-Key: <token>
    #   - Authorization: Bearer <token>
    expected_key = os.getenv("CLOUDHAND_API_KEY")
    if expected_key:
        provided = (request.headers.get("X-API-Key") or request.headers.get("Authorization") or "").strip()
        token = provided
        if token.lower().startswith("bearer "):
            token = token[7:].strip()

        if token and token == expected_key:
            api_github_id = os.getenv("CLOUDHAND_API_GITHUB_ID", "cloudhand_api_key")
            api_username = os.getenv("CLOUDHAND_API_USERNAME", "api")

            result = await db.execute(select(User).where(User.github_id == api_github_id))
            user = result.scalars().first()
            if not user:
                user = User(id=uuid.uuid4(), github_id=api_github_id, username=api_username)
                db.add(user)
                await db.commit()
                await db.refresh(user)
            return user

    user_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(
            status_code=401, detail="Invalid session", headers=_expire_session_cookie()
        )

    result = await db.execute(select(User).where(User.id == user_uuid))
    user = result.scalars().first()
    if not user:
        raise HTTPException(
            status_code=401, detail="User not found", headers=_expire_session_cookie()
        )

    return user
