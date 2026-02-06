import io
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .adapters.vault import _openbao_client


def read_secret(path: str) -> Optional[Dict[str, Any]]:
    """
    Read a KV v2 secret at the given path (relative to mount).
    Returns the data dict or None if not found/unavailable.
    """
    client_tuple = _openbao_client()
    if not client_tuple:
        return None

    client, mount_point = client_tuple
    try:
        resp = client.secrets.kv.v2.read_secret_version(path=path, mount_point=mount_point)
        return resp.get("data", {}).get("data", {})
    except Exception as exc:  # pragma: no cover - defensive
        logging.warning("OpenBao read failed for %s: %s", path, exc)
        return None


def get_secret_value(path: str, key: str, default: Optional[str] = None) -> Optional[str]:
    """
    Convenience helper to fetch a single key from a secret.
    """
    data = read_secret(path)
    if not data:
        return default
    return data.get(key, default)


def get_provider_token(provider: str, default: Optional[str] = None) -> Optional[str]:
    """
    Fetch a provider token from OpenBao at:
        <project_path>/providers/<provider>
    Falls back to the provided default if not present or OpenBao unavailable.
    """
    project_path = os.getenv("OPENBAO_PROJECT_PATH", "projects/cloudhand")
    path = f"{project_path}/providers/{provider}"
    return get_secret_value(path, "token", default=default)


def get_or_create_project_ssh_key(project_id: str) -> Tuple[str, str]:
    """
    Returns (private_key, public_key) for the given project, backed by OpenBao.
    Generates and persists a new keypair if none exists.
    """
    try:
        import paramiko
    except ImportError as exc:  # pragma: no cover - explicit dependency error
        raise RuntimeError("paramiko is required for SSH key management.") from exc

    client_tuple = _openbao_client()
    if not client_tuple:
        # Fallback: store project SSH keys locally on disk (no OpenBao required).
        # This makes the control-plane usable out-of-the-box, while still supporting
        # OpenBao when OPENBAO_TOKEN/OPENBAO_ADDR are set.
        keys_dir = Path(os.getenv("CLOUDHAND_KEYS_DIR", str(Path.home() / ".cloudhand" / "keys")))
        keys_dir.mkdir(parents=True, exist_ok=True)

        priv_path = keys_dir / f"{project_id}_id_rsa"
        pub_path = keys_dir / f"{project_id}_id_rsa.pub"

        if priv_path.exists() and pub_path.exists():
            return priv_path.read_text(encoding="utf-8"), pub_path.read_text(encoding="utf-8")

        # Generate a new keypair
        key = paramiko.RSAKey.generate(4096)
        buf = io.StringIO()
        key.write_private_key(buf)
        private_key = buf.getvalue()
        public_key = f"{key.get_name()} {key.get_base64()}"

        # Persist with restrictive permissions
        priv_path.write_text(private_key, encoding="utf-8")
        pub_path.write_text(public_key + "\n", encoding="utf-8")
        try:
            os.chmod(priv_path, 0o600)
        except Exception:
            pass

        return private_key, public_key

    client, mount_point = client_tuple
    secret_path = f"projects/{project_id}/ssh"

    # 1. Try to read existing keys.
    try:
        resp = client.secrets.kv.v2.read_secret_version(path=secret_path, mount_point=mount_point)
        data = resp.get("data", {}).get("data", {})
        if data.get("private_key") and data.get("public_key"):
            return data["private_key"], data["public_key"]
    except Exception:
        # Not found; proceed to generation.
        pass

    # 2. Generate a new RSA keypair.
    key = paramiko.RSAKey.generate(2048)
    out = io.StringIO()
    key.write_private_key(out)
    priv_str = out.getvalue()
    pub_str = f"{key.get_name()} {key.get_base64()}"

    # 3. Save for future runs.
    client.secrets.kv.v2.create_or_update_secret(
        mount_point=mount_point,
        path=secret_path,
        secret={"private_key": priv_str, "public_key": pub_str},
    )

    return priv_str, pub_str
