import asyncio
import os
import sys
from sqlalchemy import select
from uuid import UUID

# Add src to path
sys.path.append(os.path.join(os.getcwd(), "cloudhand-api", "src"))
# Also add current dir for local imports if needed
sys.path.append(os.getcwd())

from database.session import async_session
from database.models import Application, AgentSession

async def inspect_app(app_id_str):
    try:
        app_id = UUID(app_id_str)
        async with async_session() as db:
            print(f"Inspecting App ID: {app_id}")
            result = await db.execute(select(Application).where(Application.id == app_id))
            app = result.scalars().first()
            
            if not app:
                print("App not found!")
                return

            print(f"App Name: {app.name}")
            print(f"Repository Data: {app.repository}")
            
            if app.repository:
                print(f"Clone URL: {app.repository.get('clone_url')}")
                print(f"HTML URL: {app.repository.get('html_url')}")
            else:
                print("No repository data found.")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    # App ID from user message
    app_id = "e6e20594-9fbd-4dc4-8031-fd947cde5aa4"
    asyncio.run(inspect_app(app_id))
