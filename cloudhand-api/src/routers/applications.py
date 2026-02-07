from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
import uuid
import asyncio
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime
import json

from pydantic import BaseModel, Field

from cloudhand.models import ApplicationSpec

from database.connection import get_db
from database.models import Application, AgentSession, Deployment, Repository, User
from routers.deps import get_current_user
from schemas import Application as ApplicationSchema, ApplicationCreate
from services.deployment import manager
from services import onboarding as onboarding_service
import sys
router = APIRouter(tags=["applications"])

@router.websocket("/ws/{application_id}")
async def websocket_endpoint(websocket: WebSocket, application_id: str):
    app_uuid = uuid.UUID(application_id)
    await manager.connect(websocket, app_uuid)
    try:
        while True:
            await websocket.receive_text() # Keep connection alive
    except WebSocketDisconnect:
        manager.disconnect(websocket, app_uuid)

@router.get("", response_model=list[ApplicationSchema])
async def get_applications(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Application)
        .options(
            selectinload(Application.repository), 
            selectinload(Application.deployments),
            selectinload(Application.operations),
            selectinload(Application.sessions).selectinload(AgentSession.messages)
        )
        .where(Application.user_id == current_user.id)
    )
    return result.scalars().all()

@router.get("/{application_id}", response_model=ApplicationSchema)
async def get_application(
    application_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Application)
        .options(
            selectinload(Application.repository), 
            selectinload(Application.deployments),
            selectinload(Application.operations),
            selectinload(Application.sessions)
        )
        .where(
            Application.id == uuid.UUID(application_id),
            Application.user_id == current_user.id,
        )
    )
    app = result.scalar_one_or_none()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    return app

@router.post("", response_model=ApplicationSchema)
async def create_application(
    app_create: ApplicationCreate,
    auto_apply: bool = False,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo_result = await db.execute(
        select(Repository).where(
            Repository.id == app_create.repository_id,
            Repository.user_id == current_user.id,
        )
    )
    repository = repo_result.scalars().first()
    if not repository:
        raise HTTPException(status_code=404, detail="Repository not found for user")

    new_app = Application(
        id=uuid.uuid4(),
        user_id=current_user.id,
        repository_id=app_create.repository_id,
        name=app_create.name,
        status="deploying",  # Initial status
        config={}
    )
    db.add(new_app)
    
    # Create initial deployment
    deployment = Deployment(
        id=uuid.uuid4(),
        application_id=new_app.id,
        status="pending",
        logs="Initializing deployment..."
    )
    db.add(deployment)
    
    await db.commit()
    await db.refresh(new_app)
    
    # Trigger background deployment
    await manager.start_deployment(deployment.id, new_app.id, auto_apply=auto_apply)
    
    # Re-fetch with relationships
    result = await db.execute(
        select(Application)
        .options(
            selectinload(Application.repository), 
            selectinload(Application.deployments),
            selectinload(Application.operations),
            selectinload(Application.sessions).selectinload(AgentSession.messages)
        )
        .where(Application.id == new_app.id)
    )
    return result.scalar_one()

def _find_latest_plan() -> Optional[Path]:
    # /.../cloudhand/cloudhand-api/src/routers -> project root is parents[3]
    root_dir = Path(__file__).resolve().parents[3]
    ch_dir = root_dir / "cloudhand"
    plans = sorted(ch_dir.glob("plan-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return plans[0] if plans else None

class PlanUpdate(BaseModel):
    content: str = Field(..., description="Full JSON content of the plan")
    path: Optional[str] = Field(None, description="Optional plan path (inside cloudhand/)")

class ApplyPayload(BaseModel):
    plan_path: Optional[str] = None

@router.get("/plans/latest")
async def latest_plan(current_user: User = Depends(get_current_user)):
    plan_path = _find_latest_plan()
    if not plan_path:
        raise HTTPException(status_code=404, detail="No plan found")
    try:
        content = plan_path.read_text(encoding="utf-8")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read plan: {exc}") from exc
    return {"path": str(plan_path), "content": content}

@router.post("/plans")
async def save_plan(body: PlanUpdate, current_user: User = Depends(get_current_user)):
    # Validate JSON
    try:
        json.loads(body.content)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Plan content is not valid JSON: {exc}") from exc

    root_dir = Path(__file__).resolve().parents[3]
    ch_dir = root_dir / "cloudhand"
    ch_dir.mkdir(parents=True, exist_ok=True)

    if body.path:
        plan_path = Path(body.path).expanduser().resolve()
        if not str(plan_path).startswith(str(ch_dir.resolve())):
            raise HTTPException(status_code=400, detail="Plan path must be inside cloudhand directory")
    else:
        ts = datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%SZ")
        plan_path = ch_dir / f"plan-{ts}.json"

    try:
        plan_path.write_text(body.content, encoding="utf-8")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to write plan: {exc}") from exc

    return {"path": str(plan_path)}




def _deep_merge(dst: Any, patch: Any) -> Any:
    """Recursive merge for dict patches.

    - dict + dict => merge keys recursively
    - all other types (including lists) => patch overwrites dst
    """
    if isinstance(dst, dict) and isinstance(patch, dict):
        out = dict(dst)
        for k, v in patch.items():
            out[k] = _deep_merge(out.get(k), v)
        return out
    return patch


@router.post("/plans/workloads")
async def update_plan_workload(
    workload: Dict[str, Any] = Body(..., description="Workload object (patch). Must include at least a 'name'."),
    plan_path: Optional[str] = None,
    instance_name: Optional[str] = None,
    create_if_missing: bool = False,
    in_place: bool = False,
    current_user: User = Depends(get_current_user),
):
    """Patch (or optionally insert) a workload in the latest plan.

    This endpoint accepts a single workload JSON object (not a full plan).

    - Finds workloads with the same `name` inside `new_spec.instances[*].workloads[*]`
    - Deep-merges the posted object into the existing workload
    - Validates the resulting workload against `cloudhand.models.ApplicationSpec`
    - Writes a new plan file (unless `in_place=true`)
    """

    name = (workload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Workload must include a non-empty 'name' field")

    root_dir = Path(__file__).resolve().parents[3]
    ch_dir = root_dir / "cloudhand"
    ch_dir.mkdir(parents=True, exist_ok=True)

    # Select base plan
    if plan_path:
        base_plan_path = Path(plan_path).expanduser().resolve()
    else:
        base_plan_path = _find_latest_plan()

    if not base_plan_path or not base_plan_path.exists():
        raise HTTPException(status_code=404, detail="No plan found")

    # Guard: only allow plan files under cloudhand/
    if not str(base_plan_path).startswith(str(ch_dir.resolve())):
        raise HTTPException(status_code=400, detail="Plan path must be inside cloudhand directory")

    try:
        plan = json.loads(base_plan_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read plan JSON: {exc}") from exc

    new_spec = plan.get("new_spec") or {}
    instances = new_spec.get("instances") or []
    if not isinstance(instances, list):
        raise HTTPException(status_code=400, detail="Plan new_spec.instances must be a list")

    updated = 0
    for inst in instances:
        if instance_name and inst.get("name") != instance_name:
            continue
        workloads = inst.get("workloads") or []
        if not isinstance(workloads, list):
            continue
        for i, existing in enumerate(workloads):
            if (existing.get("name") or "").strip() != name:
                continue
            merged = _deep_merge(existing, workload)
            try:
                validated = ApplicationSpec.model_validate(merged)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Updated workload is invalid: {exc}") from exc
            workloads[i] = json.loads(validated.model_dump_json())
            inst["workloads"] = workloads
            updated += 1

    if updated == 0:
        if not create_if_missing:
            raise HTTPException(status_code=404, detail=f"No workload named '{name}' found in plan")

        # Choose where to insert the new workload
        target_inst = None
        if instance_name:
            target_inst = next((i for i in instances if i.get("name") == instance_name), None)
            if not target_inst:
                raise HTTPException(status_code=404, detail=f"Instance '{instance_name}' not found in plan")
        else:
            if len(instances) != 1:
                raise HTTPException(
                    status_code=400,
                    detail="instance_name is required when plan contains multiple instances",
                )
            target_inst = instances[0]

        try:
            validated = ApplicationSpec.model_validate(workload)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Workload is invalid: {exc}") from exc

        target_inst.setdefault("workloads", [])
        if not isinstance(target_inst["workloads"], list):
            target_inst["workloads"] = []
        target_inst["workloads"].append(json.loads(validated.model_dump_json()))
        updated = 1

    plan["new_spec"] = new_spec

    # Write updated plan
    if in_place:
        out_path = base_plan_path
    else:
        ts = datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%SZ")
        out_path = ch_dir / f"plan-{ts}.json"

    try:
        out_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to write updated plan: {exc}") from exc

    return {
        "status": "ok",
        "base_plan_path": str(base_plan_path),
        "updated_plan_path": str(out_path),
        "workload_name": name,
        "updated_count": updated,
    }


@router.post("/plans/apply")
async def apply_plan_direct(
    payload: Optional[ApplyPayload] = None,
    current_user: User = Depends(get_current_user),
):
    """Apply a plan directly (without requiring an Application/Deployment).

    This is the simplest "CLI-like" workflow:
      1) POST /api/applications/plans (or /plans/workloads)
      2) POST /api/applications/plans/apply

    Returns stdout/stderr logs and (if available) terraform server_ips output.
    """

    root_dir = Path(__file__).resolve().parents[3]
    ch_dir = root_dir / "cloudhand"
    tf_dir = ch_dir / "terraform"
    ch_dir.mkdir(parents=True, exist_ok=True)

    # Pick plan file
    if payload and payload.plan_path:
        plan_file = Path(payload.plan_path).expanduser().resolve()
    else:
        plan_file = _find_latest_plan()

    if not plan_file or not plan_file.exists():
        raise HTTPException(status_code=404, detail="No plan found")

    # Guard: only allow plans under cloudhand/
    if not str(plan_file).startswith(str(ch_dir.resolve())):
        raise HTTPException(status_code=400, detail="plan_path must be inside the cloudhand directory")

    # Resolve provider/token from onboarding config
    status = onboarding_service.onboarding_status()
    provider = status.get("provider")
    cfg = onboarding_service.load_provider_config(provider) if provider else {}
    token = cfg.get("token")
    if not provider or not token:
        raise HTTPException(status_code=400, detail="Provider not configured; call /api/onboarding/provider first")

    env = os.environ.copy()
    if provider == "hetzner":
        env["HCLOUD_TOKEN"] = token

    # Ensure cloudhand package is importable for subprocess
    env["PYTHONPATH"] = f"{root_dir / 'src'}:{env.get('PYTHONPATH','')}"

    # Run: python -m cloudhand.cli apply <plan> --auto-approve
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-u",
        "-m",
        "cloudhand.cli",
        "apply",
        str(plan_file),
        "--auto-approve",
        cwd=str(root_dir),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    logs: list[str] = []
    assert proc.stdout
    async for raw in proc.stdout:
        logs.append(raw.decode(errors="ignore"))

    rc = await proc.wait()

    # Try to read terraform outputs for convenience
    server_ips: dict = {}
    live_url: Optional[str] = None
    tf_out: dict = {}

    if tf_dir.exists():
        try:
            proc2 = await asyncio.create_subprocess_exec(
                "terraform",
                "output",
                "-json",
                cwd=str(tf_dir),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await proc2.communicate()
            if proc2.returncode == 0:
                tf_out = json.loads(out.decode() or "{}")
            else:
                logs.append(f"Warning: terraform output failed: {err.decode(errors='ignore')}\n")
        except Exception as exc:
            logs.append(f"Warning: failed to read terraform outputs: {exc}\n")

    if isinstance(tf_out.get("server_ips"), dict):
        val = tf_out["server_ips"].get("value")
        if isinstance(val, dict):
            server_ips = val
            if server_ips:
                live_url = f"http://{next(iter(server_ips.values()))}"

    return {
        "returncode": rc,
        "plan_path": str(plan_file),
        "server_ips": server_ips,
        "live_url": live_url,
        "logs": "".join(logs),
    }


@router.post("/{application_id}/deployments/{deployment_id}/apply", response_model=ApplicationSchema)
async def apply_deployment(
    application_id: str,
    deployment_id: str,
    payload: Optional[ApplyPayload] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    app_uuid = uuid.UUID(application_id)
    dep_uuid = uuid.UUID(deployment_id)

    result = await db.execute(
        select(Application)
        .options(selectinload(Application.deployments))
        .where(Application.id == app_uuid, Application.user_id == current_user.id)
    )
    app = result.scalar_one_or_none()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    deployment = next((d for d in app.deployments if d.id == dep_uuid), None)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")

    deployment.status = "deploying"
    await db.commit()

    plan_path = None
    if payload and payload.plan_path:
        plan_path = Path(payload.plan_path).expanduser().resolve()
    else:
        plan_path = _find_latest_plan()
    await manager.start_deployment(dep_uuid, app_uuid, auto_apply=True, plan_path=plan_path)

    result = await db.execute(
        select(Application)
        .options(
            selectinload(Application.repository), 
            selectinload(Application.deployments),
            selectinload(Application.operations),
            selectinload(Application.sessions)
        )
        .where(Application.id == app_uuid)
    )
    return result.scalar_one()
