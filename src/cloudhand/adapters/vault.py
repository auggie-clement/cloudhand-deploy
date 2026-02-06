import logging
import os
from functools import lru_cache
from typing import Any, Optional, Tuple


@lru_cache(maxsize=1)
def _openbao_client() -> Optional[Tuple[Any, str]]:
    """
    Build and cache an authenticated OpenBao (Vault) client.
    Returns (client, mount_point) or None if unavailable.
    """
    token = os.getenv("OPENBAO_TOKEN")
    if not token:
        return None

    addr = os.getenv("OPENBAO_ADDR", "http://localhost:8200")
    mount_point = os.getenv("OPENBAO_MOUNT", "secret")

    try:
        import hvac
    except ImportError:
        logging.warning("hvac not installed; skipping OpenBao integration.")
        return None

    try:
        client = hvac.Client(url=addr, token=token)
        if not client.is_authenticated():
            logging.warning("OpenBao client could not authenticate; check OPENBAO_TOKEN/ADDR.")
            return None
        return client, mount_point
    except Exception as exc:  # pragma: no cover - defensive
        logging.warning("Failed to initialize OpenBao client: %s", exc)
        return None
