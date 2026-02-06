"""
Script to manually create database tables using SQLAlchemy models.
Run this script to apply schema changes to the database.
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.append(str(Path(__file__).parent))

from database.connection import engine, Base
from database.models import (
    User, Repository, Application, Deployment,
    Project, Task, Run, Artifact, PullRequest,
    Operation, AgentSession, AgentMessage
)


async def create_tables():
    """Create all tables defined in models"""
    async with engine.begin() as conn:
        print("Creating tables...")
        await conn.run_sync(Base.metadata.create_all)
        print("✅ Database tables created successfully!")


async def drop_tables():
    """Drop all tables (use with caution!)"""
    async with engine.begin() as conn:
        print("⚠️  Dropping all tables...")
        await conn.run_sync(Base.metadata.drop_all)
        print("Tables dropped!")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--drop":
        print("⚠️  WARNING: This will drop all tables and data!")
        response = input("Are you sure? Type 'yes' to confirm: ")
        if response.lower() == "yes":
            asyncio.run(drop_tables())
            asyncio.run(create_tables())
        else:
            print("Cancelled.")
    else:
        asyncio.run(create_tables())
