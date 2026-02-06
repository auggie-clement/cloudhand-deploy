import logging
import os
import queue
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from e2b import Sandbox
except ImportError:
    Sandbox = None

logger = logging.getLogger(__name__)

class SandboxService:
    @staticmethod
    def start_run(
        repo_url: str,
        operation: str, # scan, plan, apply
        github_token: str,
        provider_config: Dict[str, Any],
        branch_name: str = "main",
        plan_description: str = "",
        run_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not Sandbox:
            logger.warning("E2B SDK not installed, skipping sandbox execution")
            return {"status": "failed", "error": "E2B SDK missing"}
        
        api_key = os.getenv("E2B_API_KEY")
        if not api_key:
            raise RuntimeError("E2B_API_KEY not configured")
        
        run_id = run_id or datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        
        logger.info(f"Starting E2B sandbox for run {run_id} ({operation})...")
        
        # Create sandbox
        sandbox = Sandbox.create()
        
        try:
            env_vars = {
                "REPO_CLONE_URL": repo_url,
                "GITHUB_TOKEN": github_token,
                "BRANCH_NAME": branch_name,
                "RUN_ID": run_id,
                "PROJECT_ID": project_id or "default",
                "WORKSPACE_ID": project_id or "default",
                "CH_OPERATION": operation,
                "CH_PLAN_DESCRIPTION": plan_description,
                "CLOUD_PROVIDER": "hetzner",
                "HCLOUD_TOKEN": provider_config.get("token", ""),
                # Pass through LLM configuration so planning can use the model instead of falling back to a no-op
                "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", ""),
                "OPENAI_MODEL": os.getenv("OPENAI_MODEL", ""),
                "AWS_ACCESS_KEY_ID": os.getenv("AWS_ACCESS_KEY_ID", ""),
                "AWS_SECRET_ACCESS_KEY": os.getenv("AWS_SECRET_ACCESS_KEY", ""),
                "AWS_S3_ENDPOINT": os.getenv("AWS_S3_ENDPOINT", ""),
            }

            if project_id:
                try:
                    from services.secrets import SecretsService
                    secrets_service = SecretsService()
                    # Issue a token with 30m TTL
                    sandbox_token = secrets_service.issue_sandbox_token(str(project_id), ttl="30m")
                    
                    env_vars.update({
                        "OPENBAO_ADDR": os.getenv("OPENBAO_ADDR", "http://host.docker.internal:8200"),
                        "OPENBAO_TOKEN": sandbox_token,
                        "OPENBAO_PROJECT_PATH": f"projects/{project_id}"
                    })
                    logger.info(f"Injected OpenBao token for project {project_id}")
                except Exception as e:
                    logger.error(f"Failed to issue OpenBao token: {e}")
                    # We don't fail the run, but secrets won't be available

            
            # Create a queue for streaming output
            import queue
            import threading
            output_queue = queue.Queue()
            queue_id = id(output_queue)
            
            def on_stdout(line):
                logger.info(f"[Sandbox stdout] {line}")
                output_queue.put(f"[stdout] {line}")
            
            def on_stderr(line):
                logger.warning(f"[Sandbox stderr] {line}")
                import sys
                sys.stderr.write(f"DEBUG: on_stderr queue_id={id(output_queue)}\n")
                output_queue.put(f"[stderr] {line}")
            
            # Function to run the ENTIRE sequence in a thread
            def run_sequence():
                try:
                    # 1. Install dependencies
                    output_queue.put("[system] Installing dependencies (this may take a minute)...")
                    install_script = """
export DEBIAN_FRONTEND=noninteractive
export PYTHONUNBUFFERED=1
sudo apt-get update && sudo apt-get install -y git wget curl unzip gnupg lsb-release
wget -O- https://apt.releases.hashicorp.com/gpg | gpg --dearmor | sudo tee /usr/share/keyrings/hashicorp-archive-keyring.gpg > /dev/null
echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/hashicorp.list
sudo apt-get update && sudo apt-get install -y terraform python3-pip python3-venv
python3 -m venv /tmp/cloudhand_env
/tmp/cloudhand_env/bin/pip install --upgrade pip
/tmp/cloudhand_env/bin/pip install boto3 requests pydantic click hvac
"""
                    # Stream dependency install output too!
                    sandbox.commands.run(
                        install_script, 
                        timeout=600,
                        on_stdout=on_stdout,
                        on_stderr=on_stderr
                    )
                    output_queue.put("[system] Dependencies installed.")
                    
                    # 2. Copy cloudhand source
                    output_queue.put("[system] Copying source code...")
                    cloudhand_root = Path(__file__).parent.parent.parent.parent
                    sandbox.commands.run("mkdir -p /tmp/cloudhand")
                    
                    if (cloudhand_root / "pyproject.toml").exists():
                        sandbox.files.write("/tmp/cloudhand/pyproject.toml", (cloudhand_root / "pyproject.toml").read_text())
                    
                    src_dir = cloudhand_root / "src"
                    if src_dir.exists():
                        for py_file in src_dir.rglob("*.py"):
                            rel_path = py_file.relative_to(cloudhand_root)
                            parent_dir = rel_path.parent
                            sandbox.commands.run(f"mkdir -p /tmp/cloudhand/{parent_dir}")
                            content = py_file.read_text()
                            sandbox.files.write(f"/tmp/cloudhand/{rel_path}", content)
                    
                    sandbox.commands.run("cd /tmp/cloudhand && /tmp/cloudhand_env/bin/pip install -e .", on_stdout=on_stdout, on_stderr=on_stderr)
                    
                    # 3. Run worker
                    output_queue.put(f"[system] Starting CloudHand worker for {operation}...")
                    worker_script_content = f"""#!/usr/bin/env python3
import sys
sys.path.insert(0, '/tmp/cloudhand/src')

from cloudhand.worker import main
main()
"""
                    sandbox.files.write("/tmp/run_worker.py", worker_script_content)
                    sandbox.commands.run("chmod +x /tmp/run_worker.py")
                    
                    result = sandbox.commands.run(
                        "/tmp/cloudhand_env/bin/python3 -u /tmp/run_worker.py",
                        envs=env_vars,
                        timeout=1200,
                        on_stdout=on_stdout,
                        on_stderr=on_stderr
                    )
                    output_queue.put({"exit_code": result.exit_code})
                    
                except Exception as e:
                    output_queue.put({"error": str(e)})
                finally:
                    output_queue.put(None) # Signal done

            # Start sequence thread
            t = threading.Thread(target=run_sequence)
            t.start()
            import sys
            sys.stderr.write(f"DEBUG: Thread started, entering generator loop. queue_id={queue_id}\n")
            
            # Yield output from queue
            collected_output = []
            while True:
                try:
                    item = output_queue.get(timeout=1)
                    sys.stderr.write(f"DEBUG: Queue got: {str(item)[:50]}\n")
                    if item is None:
                        sys.stderr.write("DEBUG: Queue got None, breaking\n")
                        break
                    
                    if isinstance(item, dict):
                        if "exit_code" in item:
                            exit_code = item["exit_code"]
                            if exit_code != 0:
                                yield f"[ERROR] Worker failed with exit code {exit_code}"
                        elif "error" in item:
                            yield f"[ERROR] {item['error']}"
                    else:
                        collected_output.append(item)
                        sys.stderr.write("DEBUG: About to yield\n")
                        yield item
                        sys.stderr.write("DEBUG: Yielded\n")
                        
                except queue.Empty:
                    if not t.is_alive():
                        sys.stderr.write("DEBUG: Thread dead and queue empty, breaking\n")
                        break
                    sys.stderr.write("DEBUG: Queue empty, continuing\n")
                    continue
            
            t.join()
            
            yield {
                "run_id": run_id,
                "status": "completed",
                "output": "\n".join(collected_output)
            }
            
        finally:
            sandbox.kill()

    @staticmethod
    def get_run_status(run_id: str, repo_name: str) -> Dict[str, Any]:
        # Check S3 for artifacts to infer status/results
        s3 = boto3.client(
            's3',
            endpoint_url=os.getenv('AWS_S3_ENDPOINT'),
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
        )
        bucket = "cloudhand-artifacts"
        prefix = f"{repo_name}/{run_id}/"
        
        artifacts = []
        try:
            response = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
            if 'Contents' in response:
                for obj in response['Contents']:
                    artifacts.append(obj['Key'])
        except Exception as e:
            logger.error(f"Failed to list artifacts: {e}")
            
        return {
            "run_id": run_id,
            "artifacts": artifacts,
            # In a real system, we'd query a DB for the run status. 
            # Here we infer from artifacts or return "unknown" if async.
            # Since our StartRun is synchronous for now, we return results there.
            "status": "completed" if artifacts else "unknown" 
        }

    @staticmethod
    def sandbox_shell(sandbox_id: str, command: str) -> Dict[str, Any]:
        # This requires keeping the sandbox alive, which we don't do in StartRun currently.
        # For the agent flow, we might need a persistent session.
        # For now, let's assume we spin up a new sandbox for "debug" if needed, 
        # OR we change StartRun to return the sandbox object (not serializable).
        # Real implementation: Store sandbox_id in DB, use Sandbox.connect(id).
        if not Sandbox:
            return {"error": "No E2B"}
            
        try:
            # Connect to existing sandbox? E2B sandboxes die when the process exits unless kept alive.
            # We need to change the architecture to keep sandboxes alive or use long-running ones.
            # For this MVP, let's create a NEW sandbox for shell commands, 
            # which implies we need to re-clone? That's slow.
            # Better: StartRun keeps sandbox alive for X minutes?
            # E2B sandboxes have a default timeout.
            
            # Let's assume for 'sandbox_shell' we start a fresh one for now, 
            # knowing it won't have the previous state unless we mount S3 or re-clone.
            # This is a limitation of the stateless MVP.
            
            sb = Sandbox.create()
            res = sb.commands.run(command)
            sb.kill()
            return {"stdout": res.stdout, "stderr": res.stderr, "exit_code": res.exit_code}
        except Exception as e:
            return {"error": str(e)}
