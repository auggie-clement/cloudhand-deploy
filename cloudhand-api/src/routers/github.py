import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from database.connection import get_db
from database.models import Repository, User
from routers.deps import get_current_user
from services.github import GitHubService
from schemas import Repository as RepositorySchema

router = APIRouter(tags=["github"])

@router.get("/repos", response_model=list[RepositorySchema])
async def list_repos(
    sync: bool = False,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    List repositories. If sync=True, fetch from GitHub and update DB.
    Otherwise return from DB.
    """
    if sync:
        if not current_user.access_token:
            print("No access token found for user")
            raise HTTPException(status_code=400, detail="No GitHub access token found")
        
        print(f"Syncing repos for user {current_user.username}")
        github_repos = await GitHubService.list_repos(current_user.access_token)
        print(f"Got {len(github_repos)} repos from GitHub service")
        
        # Update DB
        for repo_data in github_repos:
            # Check if exists
            result = await db.execute(select(Repository).where(Repository.github_id == str(repo_data["id"])))
            repo = result.scalars().first()
            
            if not repo:
                repo = Repository(
                    id=uuid.uuid4(),
                    user_id=current_user.id,
                    github_id=str(repo_data["id"]),
                    name=repo_data["name"],
                    full_name=repo_data["full_name"],
                    html_url=repo_data["html_url"],
                    language=repo_data.get("language"),
                    default_branch=repo_data.get("default_branch")
                )
                db.add(repo)
            else:
                # Update fields
                repo.name = repo_data["name"]
                repo.full_name = repo_data["full_name"]
                repo.html_url = repo_data["html_url"]
                repo.language = repo_data.get("language")
                repo.default_branch = repo_data.get("default_branch")
        
        await db.commit()

    # Return from DB
    result = await db.execute(select(Repository).where(Repository.user_id == current_user.id))
    repos = result.scalars().all()
    return repos
