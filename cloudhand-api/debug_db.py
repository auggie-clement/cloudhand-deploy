import asyncio
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from database.models import User, Application, AgentSession, Repository

# Setup DB connection
DATABASE_URL = "postgresql+asyncpg://localhost:5432/cloudhand"
engine = create_async_engine(DATABASE_URL)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def inspect_db():
    async with AsyncSessionLocal() as db:
        print("\n--- USERS ---")
        result = await db.execute(select(User))
        users = result.scalars().all()
        for u in users:
            print(f"User: {u.id} | GitHub ID: {u.github_id} | Username: {u.username}")

        print("\n--- REPOSITORIES ---")
        result = await db.execute(select(Repository))
        repos = result.scalars().all()
        print(f"Found {len(repos)} repositories")
        for r in repos:
            print(f"Repo: {r.id} | Name: {r.name} | User ID: {r.user_id} | HTML URL: {r.html_url} | Full Name: {r.full_name} | GitHub ID: {r.github_id}")

        print("\n--- APPLICATIONS ---")
        result = await db.execute(select(Application))
        apps = result.scalars().all()
        for a in apps:
            print(f"App: {a.id} | Name: {a.name} | User ID: {a.user_id} | Repo ID: {a.repository_id}")

        print("\n--- SESSIONS ---")
        result = await db.execute(select(AgentSession))
        sessions = result.scalars().all()
        for s in sessions:
            print(f"Session: {s.id} | Title: {s.title} | User ID: {s.user_id}")

if __name__ == "__main__":
    asyncio.run(inspect_db())
