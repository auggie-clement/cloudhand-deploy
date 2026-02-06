import os
import sys
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Add cloudhand to sys.path
# Assuming structure:
# /.../cloudhand/
#   cloudhand-api/src/main.py
#   src/cloudhand/
root_dir = Path(__file__).parent.parent.parent
sys.path.append(str(root_dir / "src"))
# Ensure local routers/services are importable when running via uvicorn
sys.path.append(str(Path(__file__).parent))

def _load_env():
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())

_load_env()

from routers import applications, auth, chat, github, onboarding, resources, tasks, projects, secrets, operations, agent_sessions

app = FastAPI(title="Cloudhand API")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("CLOUDHAND_FRONTEND_ORIGINS", os.getenv("CLOUDHAND_FRONTEND_ORIGIN", "http://localhost:3001")).split(",") if o.strip()],  # Must be specific for credentials
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(resources.router, prefix="/api/resources", tags=["resources"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["tasks"])
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(github.router, prefix="/api/github", tags=["github"])
app.include_router(applications.router, prefix="/api/applications", tags=["applications"])
app.include_router(onboarding.router, prefix="/api/onboarding", tags=["onboarding"])
app.include_router(projects.router, prefix="/api/projects", tags=["projects"])
app.include_router(secrets.router, prefix="/api", tags=["secrets"])
app.include_router(operations.router, prefix="/api", tags=["operations"])
app.include_router(agent_sessions.router, prefix="/api", tags=["agent_sessions"])

@app.get("/api/health")
async def health_check():
    return {"status": "ok"}
