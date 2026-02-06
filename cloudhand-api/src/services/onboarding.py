from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from cloudhand.adapters import ProviderConfig, get_adapter

DEFAULT_CONFIG_FILE = "ch.yaml"
ROOT_DIR = Path(__file__).resolve().parents[3]
CLOUDHAND_DIR = ROOT_DIR / "cloudhand"
TERRAFORM_DIR = CLOUDHAND_DIR / "terraform"
DIAGRAMS_DIR = CLOUDHAND_DIR / "diagrams"


def _config_path() -> Path:
    return ROOT_DIR / DEFAULT_CONFIG_FILE


def load_config() -> dict:
    path = _config_path()
    if not path.exists():
        return {}
    cfg: dict = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        cfg[key.strip()] = value.strip()
    return cfg


def _write_config(provider: str, project: str) -> None:
    path = _config_path()
    path.write_text(f"provider: {provider}\nproject: {project}\n", encoding="utf-8")


def _ensure_layout(provider: str, project: str) -> None:
    for directory in [CLOUDHAND_DIR, TERRAFORM_DIR, DIAGRAMS_DIR / "history"]:
        directory.mkdir(parents=True, exist_ok=True)

    gitignore = ROOT_DIR / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(
            "cloudhand/terraform/.terraform/\n"
            "cloudhand/terraform/terraform.tfstate\n"
            "cloudhand/terraform/terraform.tfstate.*\n"
            "cloudhand/terraform/*.tfplan\n"
            "cloudhand/*.json\n"
            "cloudhand/*.diff\n",
            encoding="utf-8",
        )

    _write_config(provider, project)


def _secrets_path() -> Path:
    return CLOUDHAND_DIR / "secrets.json"


def store_provider_token(provider: str, token: str) -> None:
    secrets_path = _secrets_path()
    secrets: dict = {}
    if secrets_path.exists():
        try:
            secrets = json.loads(secrets_path.read_text(encoding="utf-8"))
        except Exception:
            secrets = {}

    providers = secrets.setdefault("providers", {})
    providers[provider] = {"token": token}

    secrets_path.write_text(json.dumps(secrets, indent=2), encoding="utf-8")

    # Make token available to downstream adapters that rely on env vars.
    if provider == "hetzner":
        os.environ["HCLOUD_TOKEN"] = token


def load_provider_config(provider: str) -> ProviderConfig:
    secrets_path = _secrets_path()
    config: ProviderConfig = ProviderConfig()
    if secrets_path.exists():
        try:
            secrets = json.loads(secrets_path.read_text(encoding="utf-8"))
            provider_cfg = (secrets.get("providers") or {}).get(provider) or {}
            token = provider_cfg.get("token")
            if token:
                config["token"] = token
        except Exception:
            pass
    if provider == "hetzner" and not config.get("token"):
        token = os.getenv("HCLOUD_TOKEN")
        if token:
            config["token"] = token
    return config


def run_scan(provider: str, token: Optional[str] = None):
    adapter = get_adapter(provider)
    cfg = load_provider_config(provider)
    if token:
        cfg["token"] = token

    CLOUDHAND_DIR.mkdir(parents=True, exist_ok=True)
    graph = adapter.scan(cfg)
    out_path = CLOUDHAND_DIR / "scan.json"
    out_path.write_text(graph.model_dump_json(indent=2), encoding="utf-8")
    return graph


def configure_provider(provider: str, token: str, project: Optional[str] = None) -> dict:
    provider = provider.lower()
    project_name = project or ROOT_DIR.name

    _ensure_layout(provider, project_name)
    store_provider_token(provider, token)
    try:
        graph = run_scan(provider, token)
    except Exception as exc:
        raise ValueError(f"Failed to verify provider: {exc}") from exc

    return {
        "provider": provider,
        "project": project_name,
        "resources": len(graph.nodes),
    }


def onboarding_status() -> dict:
    cfg = load_config()
    provider = cfg.get("provider")
    secrets = {}
    secrets_path = _secrets_path()
    if secrets_path.exists():
        try:
            secrets = json.loads(secrets_path.read_text(encoding="utf-8"))
        except Exception:
            secrets = {}

    providers = secrets.get("providers") or {}
    stored = providers.get(provider or "")
    has_token = bool(stored and stored.get("token"))
    scan_path = CLOUDHAND_DIR / "scan.json"

    return {
        "provider": provider,
        "project": cfg.get("project"),
        "has_token": has_token,
        "has_scan": scan_path.exists(),
    }
