from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from database.connection import get_db
from database.models import User
import uuid
import os

from routers.deps import SESSION_COOKIE_NAME, get_current_user
from services.github import GitHubService
from schemas import User as UserSchema
router = APIRouter(tags=["auth"])

@router.get("/github/login")
async def github_login():
    # In a real app, we'd use a state parameter to prevent CSRF
    redirect_uri = f"{os.getenv('CLOUDHAND_FRONTEND_ORIGIN', 'http://localhost:3001').rstrip('/')}/auth/callback"  # Frontend callback URL
    try:
        url = GitHubService.get_login_url(redirect_uri)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"url": url}

@router.get("/github/callback")
async def github_callback(code: str, response: Response, db: AsyncSession = Depends(get_db)):
    redirect_uri = f"{os.getenv('CLOUDHAND_FRONTEND_ORIGIN', 'http://localhost:3001').rstrip('/')}/auth/callback"
    token = await GitHubService.get_access_token(code, redirect_uri)
    if not token:
        raise HTTPException(status_code=400, detail="Failed to retrieve access token")

    github_user = await GitHubService.get_user(token)
    if not github_user:
        raise HTTPException(status_code=400, detail="Failed to retrieve user info")

    # Find or create user
    result = await db.execute(select(User).where(User.github_id == str(github_user["id"])))
    user = result.scalars().first()

    if not user:
        user = User(
            id=uuid.uuid4(),
            github_id=str(github_user["id"]),
            username=github_user["login"],
            email=github_user.get("email"),
            avatar_url=github_user.get("avatar_url"),
            access_token=token
        )
        db.add(user)
    else:
        user.access_token = token
        user.username = github_user["login"]
        user.avatar_url = github_user.get("avatar_url")
    
    await db.commit()
    await db.refresh(user)

    # Set session cookie (simplified for demo, use JWT in production)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=str(user.id), 
        httponly=True,
        samesite="lax",
        secure=False, # Important for localhost
        path="/"  # Ensure cookie is sent with all paths
    )
    
    return {"status": "success", "user": user}

@router.get("/me", response_model=UserSchema)
async def get_me(current_user: User = Depends(get_current_user)) -> User:
    return current_user

@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        httponly=True,
        samesite="lax",
        secure=False,
    )
    return {"status": "success"}
