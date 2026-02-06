import json
import os
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .paths import cloudhand_dir, project_root

def build_plan_prompt(
    spec_data: dict,
    repo_plan_data: dict,
    description: str,
    provider: str,
) -> tuple[str, dict]:
    """
    Builds the system prompt and input payload for the planning LLM.
    """
    system_prompt = f"""You are a DevOps Architect. You are given a user request and a repo URL.
You must generate a valid JSON plan adhering to the DesiredStateSpec.

For 'workloads' (Applications), analyze the request or assume standard practices:
1. If Node.js (package.json): Set runtime='nodejs'. build_config.install_command='npm install'. service_config.command='npm start'.
2. If Python (requirements.txt): Set runtime='python'. build_config.install_command='pip install -r requirements.txt'. service_config.command='gunicorn app:app'.
3. If Docker (Dockerfile): Set runtime='docker'.
4. Detect ports (e.g. 3000, 8000, 8080) and set service_config.ports=[...].

Hard constraints for deployment requests:
- If the description indicates the user wants to deploy or run an application and current_spec.instances is empty, you MUST create at least:
  - one NetworkSpec (e.g. an app network in the requested region),
  - one InstanceSpec for each logical host (e.g. app server),
  - one workload entry (ApplicationSpec) on that instance.
- Do NOT return a no-op or only comments in operations for deployment intents.

DesiredStateSpec schema for provider "{provider}":
- networks[]: {{"name": str, "cidr": str}}
- instances[]: {{"name": str, "size": str, "network": str, "region"?: str, "labels"?: {{}},"workloads"?: [ApplicationSpec]}}
- ApplicationSpec: {{"name": str, "repo_url": str, "branch"?: str, "runtime": str, "build_config": {{"install_command"?: str, "build_command"?: str, "system_packages"?: [str]}}, "service_config": {{"command": str, "environment"?: {{}}, "environment_file"?: str, "environment_file_upload"?: str, "ports"?: [int], "server_names"?: [str], "https"?: bool}}, "destination_path"?: str}}
- firewalls[]: {{"name": str, "rules": [{{"direction": str, "protocol": str, "port"?: str, "cidr"?: str}}], "targets": [{{"type": str, "selector": str}}]}}
- containers[]: legacy container specs (prefer workloads on instances).

You must output a single JSON object:
{{
  "operations": [...],
  "new_spec": {{ ... }}
}}

Output ONLY the JSON object with valid JSON."""

    input_payload = {
        "description": description,
        "current_spec": spec_data,
        "repo_plan": repo_plan_data,
    }
    
    return system_prompt, input_payload

def parse_plan_response(response_data: dict) -> dict:
    """
    Parses the response from the LLM into a plan dictionary.
    """
    # Prefer aggregated text if available.
    content = response_data.get("output_text") or ""
    if not content:
        parts: list[str] = []
        for item in response_data.get("output", []):
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    parts.append(c.get("text", ""))
        content = "".join(parts)

    # Strip Markdown fences
    if "```" in content:
        parts = content.split("```")
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if part.lower().startswith("json"):
                part = part[4:].lstrip()
            if part.startswith("{"):
                content = part
                break

    return json.loads(content)

def generate_plan(
    root: Path,
    description: str,
    provider: Optional[str] = None,
    openai_api_key: Optional[str] = None,
    openai_model: Optional[str] = None,
) -> tuple[dict, Path]:
    ch_dir = cloudhand_dir(root)
    ch_dir.mkdir(parents=True, exist_ok=True)

    spec_path = ch_dir / "spec.json"
    if not spec_path.exists():
        raise FileNotFoundError(f"spec.json not found at {spec_path}. Run 'ch sync-spec' first.")

    spec_data = json.loads(spec_path.read_text(encoding="utf-8"))

    # Try to load repo-plan.json
    repo_plan_path = project_root(root) / "repo-plan.json"
    repo_plan_data = {}
    if repo_plan_path.exists():
        try:
            repo_plan_data = json.loads(repo_plan_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    provider = provider or spec_data.get("provider") or "hetzner"
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%SZ")
    plan_id = ts

    api_key = openai_api_key or os.getenv("OPENAI_API_KEY")
    # Default to a reasoning-capable model; prefer gpt-5.1 if available.
    model = openai_model or os.getenv("OPENAI_MODEL", "gpt-5.1")

    # Default fallback plan body (no-op)
    fallback_body = {
        "operations": [],
        "new_spec": spec_data,
        "info": "LLM was not used; this plan contains no changes.",
    }

    plan_body: dict
    if not api_key:
        plan_body = fallback_body
    else:
        import requests as _requests
        
        system_prompt, input_payload = build_plan_prompt(spec_data, repo_plan_data, description, provider)

        try:
            resp = _requests.post(
                "https://api.openai.com/v1/responses",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "reasoning": {"effort": "high"},
                    "instructions": system_prompt,
                    "input": json.dumps(input_payload),
                },
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()
            plan_body = parse_plan_response(data)
    
            # Validate that deployment requests produce actual infrastructure
            new_spec = plan_body.get("new_spec", {})
            instances = new_spec.get("instances", [])
            
            # If description mentions deploying/running an app and spec has no instances, reject it
            deploy_keywords = ["deploy", "run", "create", "provision", "setup", "install"]
            is_deployment = any(keyword in description.lower() for keyword in deploy_keywords)
            
            if is_deployment and not instances:
                raise ValueError(
                    "Planning failed: the new spec has no instances defined for a deployment request. "
                    "The planner must create at least one instance to host the application."
                )
            
            logging.info("Plan validation passed: %d instances, %d containers", 
                         len(instances), len(new_spec.get("containers", [])))
        except Exception as exc:
            # Fallback if LLM fails
            plan_body = fallback_body
            plan_body["error"] = str(exc)

    # Save plan artifact
    plan_path = ch_dir / f"plan-{plan_id}.json"
    plan_path.write_text(json.dumps(plan_body, indent=2), encoding="utf-8")

    return plan_body, plan_path
