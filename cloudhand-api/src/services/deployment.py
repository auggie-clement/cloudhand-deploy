import asyncio
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from uuid import UUID

from sqlalchemy.future import select
from sqlalchemy.orm import selectinload


from cloudhand.terraform_gen import get_generator
from database.connection import AsyncSessionLocal
from database.models import Application, Deployment
from services import onboarding as onboarding_service
from services.github import GitHubService

logger = logging.getLogger(__name__)

class DeploymentManager:
    def __init__(self):
        self.active_deployments: Dict[UUID, asyncio.Task] = {}
        self.websockets: Dict[UUID, List] = {} # app_id -> list of websockets

    async def connect(self, websocket, app_id: UUID):
        await websocket.accept()
        if app_id not in self.websockets:
            self.websockets[app_id] = []
        self.websockets[app_id].append(websocket)

    def disconnect(self, websocket, app_id: UUID):
        if app_id in self.websockets:
            self.websockets[app_id].remove(websocket)
            if not self.websockets[app_id]:
                del self.websockets[app_id]

    async def broadcast(self, app_id: UUID, message: dict):
        if app_id in self.websockets:
            for connection in self.websockets[app_id]:
                try:
                    await connection.send_json(message)
                except Exception as e:
                    logger.error(f"Failed to send to websocket: {e}")

    async def start_deployment(self, deployment_id: UUID, app_id: UUID, auto_apply: bool = True, plan_path: Optional[Path] = None):
        task = asyncio.create_task(self._run_deployment(deployment_id, app_id, auto_apply=auto_apply, plan_path=plan_path))
        self.active_deployments[deployment_id] = task

    async def _append_log(self, db, deployment: Deployment, app_id: UUID, line: str, status: Optional[str] = None):
        timestamp = datetime.now().strftime("%H:%M:%S")
        entry = f"[{timestamp}] {line}\n"
        deployment.logs = (deployment.logs or "") + entry
        if status:
            deployment.status = status
        await db.commit()
        await self.broadcast(
            app_id,
            {
                "type": "log_update",
                "deployment_id": str(deployment.id),
                "status": deployment.status,
                "log_chunk": entry,
                "full_logs": deployment.logs,
            },
        )

    async def _run_cmd(self, cmd: list[str], cwd: Optional[Path], env: dict, db, deployment: Deployment, app_id: UUID):
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert proc.stdout
        async for raw in proc.stdout:
            line = raw.decode(errors="ignore").rstrip()
            await self._append_log(db, deployment, app_id, line)
        return await proc.wait()

    async def _find_latest_plan(self, ch_dir: Path) -> Optional[Path]:
        plans = sorted(ch_dir.glob("plan-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        return plans[0] if plans else None

    async def _run_deployment(self, deployment_id: UUID, app_id: UUID, auto_apply: bool, plan_path: Optional[Path]):
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Application)
                    .options(selectinload(Application.repository), selectinload(Application.user))
                    .where(Application.id == app_id)
                )
                app = result.scalars().first()
                deployment = await db.get(Deployment, deployment_id)
                if not app or not deployment:
                    return

                repo_name = app.repository.full_name if app.repository else "repository"

                # Resolve provider/token
                status = onboarding_service.onboarding_status()
                provider = status.get("provider")
                cfg = onboarding_service.load_provider_config(provider) if provider else {}
                token = cfg.get("token")
                if not provider or not token:
                    deployment.status = "failed"
                    deployment.logs = (deployment.logs or "") + "Provider not configured; cannot deploy.\n"
                    await db.commit()
                    await self.broadcast(app_id, {"type": "error", "message": "Provider not configured"})
                    return

                # Update commit hash
                if app.repository and app.user and app.user.access_token:
                    try:
                        commit_sha = await GitHubService.get_latest_commit(
                            app.repository.full_name, app.user.access_token
                        )
                        if commit_sha:
                            deployment.commit_hash = commit_sha
                            await db.commit()
                    except Exception as exc:
                        await self._append_log(db, deployment, app_id, f"Warning: failed to fetch commit: {exc}")

                root_dir = Path(__file__).resolve().parents[3]
                ch_dir = root_dir / "cloudhand"
                tf_dir = ch_dir / "terraform"
                ch_dir.mkdir(parents=True, exist_ok=True)

                env = os.environ.copy()
                if provider == "hetzner":
                    env["HCLOUD_TOKEN"] = token
                env["PYTHONPATH"] = f"{root_dir / 'src'}:{env.get('PYTHONPATH','')}"
                # Ensure LLM access for planning
                env.setdefault("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", ""))

                if plan_path is None:
                    # 1. ch scan
                    await self._append_log(db, deployment, app_id, "Running ch scan...", status="deploying")
                    rc = await self._run_cmd(
                        ["python", "-m", "cloudhand.cli", "scan", "--provider", provider],
                        cwd=root_dir,
                        env=env,
                        db=db,
                        deployment=deployment,
                        app_id=app_id,
                    )
                    if rc != 0:
                        await self._append_log(db, deployment, app_id, "ch scan failed", status="failed")
                        return

                    # 2. ch sync-spec
                    await self._append_log(db, deployment, app_id, "Running ch sync-spec...")
                    rc = await self._run_cmd(
                        ["python", "-m", "cloudhand.cli", "sync-spec"],
                        cwd=root_dir,
                        env=env,
                        db=db,
                        deployment=deployment,
                        app_id=app_id,
                    )
                    if rc != 0:
                        await self._append_log(db, deployment, app_id, "ch sync-spec failed", status="failed")
                        return

                    # 3. ch plan - emphasize isolation/new project
                    desc = (
                        f"Deploy {repo_name} on Hetzner with Docker, HTTP on 80. "
                        "Create a NEW isolated project/network/server for this app; do not touch existing resources."
                    )
                    if not env.get("OPENAI_API_KEY"):
                        await self._append_log(
                            db,
                            deployment,
                            app_id,
                            "OPENAI_API_KEY is not set; cannot generate LLM-based plan. Set it in .env.",
                            status="failed",
                        )
                        return

                    await self._append_log(db, deployment, app_id, f"Running ch plan: {desc}")
                    rc = await self._run_cmd(
                        ["python", "-m", "cloudhand.cli", "plan", desc],
                        cwd=root_dir,
                        env=env,
                        db=db,
                        deployment=deployment,
                        app_id=app_id,
                    )
                    if rc != 0:
                        await self._append_log(db, deployment, app_id, "ch plan failed", status="failed")
                        return

                    plan_file = await self._find_latest_plan(ch_dir)
                else:
                    plan_file = plan_path

                if not plan_file:
                    await self._append_log(db, deployment, app_id, "No plan file found", status="failed")
                    return

                if not auto_apply:
                    deployment.status = "planned"
                    await db.commit()
                    await self._append_log(db, deployment, app_id, f"Plan ready at {plan_file}", status="planned")
                    await self.broadcast(app_id, {"type": "app_update", "status": "planned"})
                    return

                # 4. ch apply
                await self._append_log(db, deployment, app_id, f"Applying plan {plan_file.name} ...")
                rc = await self._run_cmd(
                    ["python", "-m", "cloudhand.cli", "apply", str(plan_file), "--auto-approve"],
                    cwd=root_dir,
                    env=env,
                    db=db,
                    deployment=deployment,
                    app_id=app_id,
                )
                if rc != 0:
                    await self._append_log(db, deployment, app_id, "ch apply failed", status="failed")
                    return

                # Capture Terraform outputs for UI visibility.
                tf_out = {}
                try:
                    proc = await asyncio.create_subprocess_exec(
                        "terraform",
                        "output",
                        "-json",
                        cwd=tf_dir,
                        env=env,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, stderr = await proc.communicate()
                    if proc.returncode == 0:
                        tf_out = json.loads(stdout.decode() or "{}")
                    else:
                        await self._append_log(
                            db, deployment, app_id, f"Warning: terraform output failed: {stderr.decode()}"
                        )
                except Exception as exc:
                    await self._append_log(db, deployment, app_id, f"Warning: failed to read terraform outputs: {exc}")

                server_ips = {}
                if isinstance(tf_out.get("server_ips"), dict):
                    server_ips = tf_out["server_ips"].get("value") or {}

                # Persist current state so the UI can show deployed servers.
                app.current_state = {
                    "server_ips": server_ips,
                    "live_url": next(iter(server_ips.values()), None) and f"http://{next(iter(server_ips.values()))}"
                }

                deployment.status = "running"
                await db.commit()
                await self._append_log(db, deployment, app_id, "Deployment successful!", status="running")
                app.status = "running"
                await db.commit()
                await self.broadcast(app_id, {"type": "app_update", "status": "running", "current_state": app.current_state})

        except Exception as e:
            logger.error("Deployment failed: %s", e)
            async with AsyncSessionLocal() as db:
                deployment = await db.get(Deployment, deployment_id)
                if deployment:
                    deployment.status = "failed"
                    deployment.logs = (deployment.logs or "") + f"\n[ERROR] Deployment failed: {str(e)}"
                    await db.commit()
                    await self.broadcast(app_id, {"type": "error", "message": str(e)})

manager = DeploymentManager()
