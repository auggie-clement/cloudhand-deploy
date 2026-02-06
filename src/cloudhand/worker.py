import os
import sys
import json
import subprocess
import boto3
import shutil
import requests
from pathlib import Path
from typing import Optional

# Import core functions
# Note: In the sandbox, cloudhand is installed in editable mode or as a package
from cloudhand.core.scan import run_scan
from cloudhand.core.spec import sync_spec
from cloudhand.core.plan import generate_plan
from cloudhand.core.apply import apply_plan
from cloudhand.core.diagram import generate_diagram
from cloudhand.core.terraform import generate_terraform
from cloudhand.adapters import get_adapter, ProviderConfig
from cloudhand.secrets import get_provider_token, get_secret_value
from cloudhand.core.paths import terraform_dir
from cloudhand.models import DesiredStateSpec

def run_cmd(cmd, check=True):
    print(f"Running: {cmd}", flush=True)
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    print(f"Output: {result.stdout}")
    if result.stderr:
        print(f"Error: {result.stderr}", file=sys.stderr)
    if check and result.returncode != 0:
        print(f"Command failed with exit code {result.returncode}", file=sys.stderr)
        sys.exit(result.returncode)
    return result

def ensure_s3_bucket(s3, bucket: str):
    """Ensure S3 bucket exists, creating it if necessary."""
    try:
        s3.head_bucket(Bucket=bucket)
        print(f"S3 bucket '{bucket}' exists.")
        return True
    except Exception as e:
        print(f"Bucket doesn't exist, attempting to create: {e}")
        try:
            # Try with fsn1 constraint first (Hetzner default)
            s3.create_bucket(
                Bucket=bucket,
                CreateBucketConfiguration={'LocationConstraint': 'fsn1'}
            )
            print(f"Created S3 bucket '{bucket}' with fsn1 constraint.")
            return True
        except Exception as create_error:
            print(f"Bucket creation failed with fsn1 constraint: {create_error}")
            try:
                # Try without constraint (standard S3)
                s3.create_bucket(Bucket=bucket)
                print(f"Created S3 bucket '{bucket}' without constraint.")
                return True
            except Exception as retry_error:
                print(f"Bucket creation failed without constraint: {retry_error}")
                return False

def upload_to_s3(local_path: Path, s3_key: str, s3_client=None, bucket: str = None):
    if s3_client is None:
        s3_client = boto3.client(
            's3',
            region_name='eu-central-1',  # or us-east-1 for compatibility
            endpoint_url=os.getenv('AWS_S3_ENDPOINT'),
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
        )
    
    if bucket is None:
        bucket = "cloudhand-artifacts"
    
    try:
        print(f"Uploading {local_path} to s3://{bucket}/{s3_key}")
        s3_client.upload_file(str(local_path), bucket, s3_key)
        print(f"✓ Successfully uploaded {s3_key}")
    except Exception as e:
        print(f"Failed to upload to S3: {e}", file=sys.stderr)
        raise

def main():
    # Configuration from environment
    repo_url = os.getenv('REPO_CLONE_URL')
    branch = os.getenv('BRANCH_NAME', 'main')
    github_token = (
        os.getenv('GITHUB_TOKEN')
        or os.getenv('GH_TOKEN')
        or os.getenv('GITHUB_PAT')
        or ''
    )
    github_token = get_provider_token("github", default=github_token) or github_token
    run_id = os.getenv('RUN_ID', 'latest')
    operation = os.getenv('CH_OPERATION', 'scan') # scan, plan, apply
    plan_description = os.getenv('CH_PLAN_DESCRIPTION', '')
    
    # Setup workspace
    workspace_root = Path("/tmp/workspace")
    workspace_root.mkdir(exist_ok=True)
    os.chdir(workspace_root)
    
    # Clone repository
    print(f"Cloning {repo_url} branch {branch}...")
    # Clean up workspace first
    run_cmd("rm -rf * .git .gitignore .github")
    
    if github_token and 'github.com' in repo_url:
        auth_url = repo_url.replace('https://', f'https://{github_token}@')
        run_cmd(f'git clone --branch {branch} {auth_url} .')
    else:
        run_cmd(f'git clone --branch {branch} {repo_url} .')
        
    print("Repository cloned successfully!")
    
    # Provider config
    provider = "hetzner" # TODO: Make configurable
    
    # Secrets: prefer OpenBao, fall back to env vars
    hcloud_token = get_provider_token("hetzner", default=os.getenv('HCLOUD_TOKEN', '')) or ''
    github_token = get_provider_token("github", default=github_token) or github_token

    provider_config = ProviderConfig(
        token=hcloud_token,
        # Add other provider specific configs here
    )

    def import_existing_servers(spec_path: Path, token: str) -> None:
        """Import existing Hetzner servers into Terraform state to avoid recreating them."""
        if provider != "hetzner" or not token:
            return
        tf_bin = shutil.which("terraform")
        if not tf_bin:
            print("Terraform not available; skipping imports.")
            return

        try:
            spec_data = json.loads(spec_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"Warning: unable to read spec for import: {exc}")
            return

        tf_dir = terraform_dir(workspace_root)
        env = os.environ.copy()
        env["TF_VAR_hcloud_token"] = token
        # Ensure plugins/lockfile exist before import
        subprocess.run([tf_bin, f"-chdir={tf_dir}", "init", "-input=false"], check=False, env=env)
        for inst in spec_data.get("instances", []):
            name = inst.get("name")
            if not name:
                continue
            res_name = name.replace("-", "_")
            try:
                resp = requests.get(
                    "https://api.hetzner.cloud/v1/servers",
                    params={"name": name},
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=15,
                )
                resp.raise_for_status()
                servers = resp.json().get("servers", [])
                if not servers:
                    print(f"No existing server found for {name}, skipping import.")
                    continue
                server_id = servers[0]["id"]
                print(f"Importing existing server {name} (id {server_id}) into state...")
                subprocess.run(
                    [tf_bin, f"-chdir={tf_dir}", "import", f"hcloud_server.{res_name}", str(server_id)],
                    check=False,
                    env=env,
                )
            except Exception as exc:
                print(f"Warning: import for {name} failed: {exc}")
    
    # Initialize S3 client and ensure bucket exists
    # Prefer OpenBao S3 creds, fall back to env
    aws_access_key = get_secret_value("projects/cloudhand/providers/s3", "access_key", os.getenv("AWS_ACCESS_KEY_ID"))
    aws_secret_key = get_secret_value("projects/cloudhand/providers/s3", "secret_key", os.getenv("AWS_SECRET_ACCESS_KEY"))

    s3_client = boto3.client(
        's3',
        region_name='eu-central-1',  # Hetzner fsn1 region
        endpoint_url=os.getenv('AWS_S3_ENDPOINT'),
        aws_access_key_id=aws_access_key,
        aws_secret_access_key=aws_secret_key
    )
    bucket_name = "cloudhand-artifacts"
    
    print(f"\n=== Ensuring S3 bucket '{bucket_name}' exists ===")
    bucket_ready = ensure_s3_bucket(s3_client, bucket_name)
    if not bucket_ready:
        print("Note: S3 bucket not available. Artifacts won't be uploaded, but deployment will continue.")
    
    try:
        # Execute requested operation
        if operation == 'scan':
            print("Running scan...")
            run_scan(workspace_root, provider, provider_config)
            spec = sync_spec(workspace_root, provider)
            generate_terraform(workspace_root, spec)
            import_existing_servers(workspace_root / "cloudhand" / "spec.json", hcloud_token)
            generate_diagram(workspace_root)
            
        elif operation == 'plan':
            print(f"Running plan: {plan_description}")
            # Ensure we have a spec first
            if not (workspace_root / "cloudhand" / "spec.json").exists():
                run_scan(workspace_root, provider, provider_config)
                spec = sync_spec(workspace_root, provider)
            else:
                spec_data = json.loads((workspace_root / "cloudhand" / "spec.json").read_text(encoding="utf-8"))
                spec = DesiredStateSpec.model_validate(spec_data)
            generate_terraform(workspace_root, spec)
            import_existing_servers(workspace_root / "cloudhand" / "spec.json", hcloud_token)
            
            generate_plan(
                workspace_root, 
                description=plan_description,
                provider=provider
            )
            
        elif operation == 'apply':
            print("Running apply...")
            
            print("Ensuring state...")
            run_scan(workspace_root, provider, provider_config)
            spec = sync_spec(workspace_root, provider)
            generate_terraform(workspace_root, spec)
            import_existing_servers(workspace_root / "cloudhand" / "spec.json", hcloud_token)

            # Try to re-use the most recent plan artifact for this repo from S3.
            repo_name = repo_url.split('/')[-1].replace('.git', '')
            plan_path: Optional[Path] = None

            if bucket_ready:
                try:
                    print("Looking for latest plan artifact in S3...")
                    prefix = f"{repo_name}/"
                    continuation_token = None
                    latest_obj = None

                    while True:
                        kwargs = {"Bucket": bucket_name, "Prefix": prefix, "MaxKeys": 1000}
                        if continuation_token:
                            kwargs["ContinuationToken"] = continuation_token

                        resp = s3_client.list_objects_v2(**kwargs)
                        for obj in resp.get("Contents", []):
                            key = obj["Key"]
                            if "/plan-" in key and key.endswith(".json"):
                                if (latest_obj is None) or (obj["LastModified"] > latest_obj["LastModified"]):
                                    latest_obj = obj

                        if resp.get("IsTruncated"):
                            continuation_token = resp.get("NextContinuationToken")
                        else:
                            break

                    if latest_obj:
                        key = latest_obj["Key"]
                        print(f"Downloading latest plan from s3://{bucket_name}/{key}")
                        ch_dir = workspace_root / "cloudhand"
                        ch_dir.mkdir(parents=True, exist_ok=True)
                        plan_path = ch_dir / "plan-from-s3.json"
                        s3_client.download_file(bucket_name, key, str(plan_path))
                        print(f"✓ Downloaded plan to {plan_path}")
                    else:
                        print("No plan artifact found in S3; will generate a fresh plan.")
                except Exception as err:
                    print(f"Warning: Failed to download plan from S3 ({err}); will generate a fresh plan.")

            if not plan_path:
                print("Generating plan for apply...")
                plan_body, plan_path = generate_plan(
                    workspace_root, 
                    description=plan_description or "Apply changes",
                    provider=provider
                )
            
            print("Applying plan...")
            # Pass HCLOUD_TOKEN as env var for Terraform
            os.environ["TF_VAR_hcloud_token"] = provider_config.get("token", "")
            github_token = (
                os.getenv("GITHUB_TOKEN")
                or os.getenv("GH_TOKEN")
                or os.getenv("GITHUB_PAT")
                or github_token
            )
            github_token = get_provider_token("github", default=github_token) or ""
            if github_token:
                os.environ["TF_VAR_github_token"] = github_token
                os.environ["GITHUB_TOKEN"] = github_token
            
            project_id = os.getenv("PROJECT_ID", "default")
            workspace_id = os.getenv("WORKSPACE_ID", "default")
            
            exit_code = apply_plan(
                workspace_root, 
                plan_path, 
                auto_approve=True,
                project_id=project_id,
                workspace_id=workspace_id
            )
            
            if exit_code != 0:
                print(f"Apply failed with exit code {exit_code}", file=sys.stderr)
                sys.exit(exit_code)
                
            print("Apply successful!")
            
            # Capture outputs
            print("\\nTerraform Outputs:")
            tf_dir = workspace_root / "cloudhand" / "terraform"
            run_cmd(f"cd {tf_dir} && terraform output -json")
            
            # Also print public IPs specifically if possible
            # We can parse the state or output
            # For now, just dumping output is good proof.

        # Upload artifacts (optional - don't fail if S3 unavailable)
        if not bucket_ready:
            print("\nS3 unavailable; skipping artifact upload.")
        else:
            try:
                print("\n=== Uploading artifacts to S3 ===")
                repo_name = repo_url.split('/')[-1].replace('.git', '')
                
                ch_dir = workspace_root / "cloudhand"
                if ch_dir.exists():
                    uploaded_count = 0
                    for path in ch_dir.rglob('*'):
                        if path.is_file():
                            rel_path = path.relative_to(ch_dir)
                            s3_key = f"{repo_name}/{run_id}/{rel_path}"
                            try:
                                upload_to_s3(path, s3_key, s3_client, bucket_name)
                                uploaded_count += 1
                            except Exception as upload_err:
                                print(f"Warning: Failed to upload {rel_path}: {upload_err}")
                    print(f"\n✓ Uploaded {uploaded_count} artifacts to S3")
            except Exception as s3_error:
                print(f"\nWarning: S3 upload failed: {s3_error}", file=sys.stderr)
                print("Continuing without S3 artifacts...")
                    
    except Exception as e:
        print(f"Operation failed: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
