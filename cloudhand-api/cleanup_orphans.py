import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, delete
from database.models import Application, Repository

# Setup DB connection
DATABASE_URL = "postgresql+asyncpg://localhost:5432/cloudhand"
engine = create_async_engine(DATABASE_URL)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def cleanup_orphans():
    async with AsyncSessionLocal() as db:
        # Find all apps
        result = await db.execute(select(Application))
        apps = result.scalars().all()
        
        # Find all repo IDs
        result = await db.execute(select(Repository.id))
        repo_ids = set(result.scalars().all())
        
        orphans = []
        for app in apps:
            if app.repository_id not in repo_ids:
                orphans.append(app.id)
        
        print(f"Found {len(orphans)} orphaned applications.")
        
        if orphans:
            await db.execute(delete(Application).where(Application.id.in_(orphans)))
            await db.commit()
            print(f"Deleted {len(orphans)} orphaned applications.")

if __name__ == "__main__":
    asyncio.run(cleanup_orphans())
