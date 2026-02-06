import json
import os
import shutil
import subprocess
from pathlib import Path

from ..adapters.deployer import ServerDeployer
from ..models import ApplicationSpec, DesiredStateSpec
from ..secrets import get_or_create_project_ssh_key
from ..terraform_gen import get_generator
from .paths import cloudhand_dir, terraform_dir


def apply_plan(
    root: Path,
    plan_path: Path,
    auto_approve: bool = False,
    terraform_bin: str = "terraform",
    project_id: str = "default",
    workspace_id: str = "default",
) -> int:
    if not plan_path.exists():
        raise FileNotFoundError(f"Plan file not found at {plan_path}")

    plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
    new_spec_data = plan_data.get("new_spec")
    if not new_spec_data:
        raise ValueError("Plan does not contain 'new_spec'")

    # Validate and persist new spec
    new_spec = DesiredStateSpec.model_validate(new_spec_data)

    ch_dir = cloudhand_dir(root)
    ch_dir.mkdir(parents=True, exist_ok=True)
    spec_path = ch_dir / "spec.json"
    spec_path.write_text(new_spec.model_dump_json(indent=2), encoding="utf-8")

    # Regenerate Terraform to reflect the new spec
    generator = get_generator(new_spec.provider)
    tf_dir = terraform_dir(root)
    generator.generate(new_spec, tf_dir, project_id, workspace_id)

    # Acquire or create project SSH keypair
    print("Fetching SSH Identity...")
    priv_key, pub_key = get_or_create_project_ssh_key(project_id)

    # Run Terraform Apply
    tf_bin = shutil.which(terraform_bin)
    if not tf_bin:
        raise FileNotFoundError(f"Terraform binary '{terraform_bin}' not found in PATH")

    env = os.environ.copy()
    if "TF_VAR_hcloud_token" not in env and os.getenv("HCLOUD_TOKEN"):
        env["TF_VAR_hcloud_token"] = os.getenv("HCLOUD_TOKEN", "")
    env["TF_VAR_ssh_public_key"] = pub_key

    subprocess.run([tf_bin, "init", "-input=false", "-upgrade"], cwd=tf_dir, check=True, env=env)

    cmd = [tf_bin, "apply"]
    if auto_approve:
        cmd.append("-auto-approve")

    result = subprocess.run(cmd, cwd=tf_dir, env=env)
    if result.returncode != 0:
        return result.returncode

    # Load Terraform outputs for server IP mapping
    tf_out = subprocess.check_output([tf_bin, "output", "-json"], cwd=tf_dir, env=env)
    outputs = json.loads(tf_out)
    server_ips = outputs.get("server_ips", {}).get("value", {})

    # Deploy workloads over SSH
    print("Deploying Applications...")
    nginx_mode = os.getenv("CLOUDHAND_NGINX_MODE", "per-app").strip().lower()
    for inst in new_spec.instances:
        ip = server_ips.get(inst.name)
        if not ip or not inst.workloads:
            continue
        print(f" Configuring {inst.name} ({ip})...")
        deployer = ServerDeployer(ip, priv_key, local_root=root)

        # Nginx routing modes:
        # - per-app (default): each workload gets its own nginx site (domain -> workload)
        # - combined: one nginx site proxies multiple workloads on a single host (path-based routing)
        if nginx_mode in {"combined", "single", "shared"}:
            deployed_apps = []
            for app in inst.workloads:
                app_model = app if isinstance(app, ApplicationSpec) else ApplicationSpec.model_validate(app)
                print(f"  -> Deploying {app_model.name} ({app_model.runtime})...")
                deployer.deploy(app_model, configure_nginx=False)
                deployed_apps.append(app_model)
            if deployed_apps:
                deployer.configure_combined_nginx(deployed_apps)
        else:
            for app in inst.workloads:
                app_model = app if isinstance(app, ApplicationSpec) else ApplicationSpec.model_validate(app)
                print(f"  -> Deploying {app_model.name} ({app_model.runtime})...")
                deployer.deploy(app_model, configure_nginx=True)

    return 0

