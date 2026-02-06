import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, selectinload
from sqlalchemy import select
from database.models import Application
from schemas import Application as ApplicationSchema

# Setup DB connection
DATABASE_URL = "postgresql+asyncpg://localhost:5432/cloudhand"
engine = create_async_engine(DATABASE_URL)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def test_query():
    async with AsyncSessionLocal() as db:
        try:
            print("Querying applications with ALL relationships...")
            result = await db.execute(
                select(Application)
                .options(
                    selectinload(Application.repository),
                    selectinload(Application.deployments),
                    selectinload(Application.operations),
                    selectinload(Application.sessions)
                )
                .limit(1)
            )
            apps = result.scalars().all()
            print(f"Found {len(apps)} applications")
            
            if apps:
                app = apps[0]
                print(f"\nApplication: {app.name}")
                print(f"Repository: {app.repository.name if app.repository else 'None'}")
                print(f"Deployments: {len(app.deployments)}")
                print(f"Operations: {len(app.operations)}")
                print(f"Sessions: {len(app.sessions)}")
                
                # Try to serialize with Pydantic
                print("\nAttempting Pydantic serialization...")
                schema = ApplicationSchema.model_validate(app)
                print("SUCCESS: Pydantic serialization worked!")
                print(f"Serialized app: {schema.name}")
        except Exception as e:
            print(f"ERROR: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_query())
