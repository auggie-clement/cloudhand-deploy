import json
import os
from pathlib import Path
from ..adapters import ProviderConfig
from .paths import cloudhand_dir

DEFAULT_CONFIG_FILE = "ch.yaml"

def load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    content = path.read_text(encoding="utf-8")
    cfg: dict = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        cfg[key.strip()] = value.strip()
    return cfg

from typing import Optional

def load_provider_config(provider: str, root: Optional[Path] = None) -> ProviderConfig:
    config: ProviderConfig = ProviderConfig()
    # Try provider-specific secrets first
    secrets_path = cloudhand_dir(root) / "secrets.json"
    if secrets_path.exists():
        try:
            secrets = json.loads(secrets_path.read_text(encoding="utf-8"))
            provider_cfg = (secrets.get("providers") or {}).get(provider) or {}
            token = provider_cfg.get("token")
            if token:
                config["token"] = token
                return config
        except Exception:
            # Fall back to env vars if secrets file is unreadable
            pass
    if provider == "hetzner":
        token = os.getenv("HCLOUD_TOKEN")
        if token:
            config["token"] = token
    return config
