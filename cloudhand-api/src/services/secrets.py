import os
import hvac
from typing import Dict, List, Optional
from pydantic import BaseModel

class SecretMetadata(BaseModel):
    name: str
    created_time: str
    version: int

class SecretsService:
    def __init__(self):
        self.client = hvac.Client(
            url=os.getenv("OPENBAO_ADDR", "http://localhost:8200"),
            token=os.getenv("OPENBAO_TOKEN", "root")
        )
        if not self.client.is_authenticated():
            print("WARNING: OpenBao client is not authenticated")

    def _get_project_path(self, project_id: str) -> str:
        return f"secret/data/projects/{project_id}"

    def set_secret(self, project_id: str, name: str, value: Dict[str, str]):
        """
        Store a secret for a project.
        Path: secret/data/projects/<project_id>/<name>
        """
        path = f"{self._get_project_path(project_id)}/{name}"
        # mount_point is usually 'secret' for KV v2
        self.client.secrets.kv.v2.create_or_update_secret(
            path=f"projects/{project_id}/{name}",
            secret=value,
            mount_point="secret"
        )

    def get_secret(self, project_id: str, name: str) -> Optional[Dict[str, str]]:
        """
        Retrieve a secret.
        """
        try:
            response = self.client.secrets.kv.v2.read_secret_version(
                path=f"projects/{project_id}/{name}",
                mount_point="secret"
            )
            return response['data']['data']
        except hvac.exceptions.InvalidPath:
            return None

    def list_secrets(self, project_id: str) -> List[str]:
        """
        List all secrets for a project.
        """
        try:
            response = self.client.secrets.kv.v2.list_secrets(
                path=f"projects/{project_id}",
                mount_point="secret"
            )
            return response['data']['keys']
        except hvac.exceptions.InvalidPath:
            return []

    def delete_secret(self, project_id: str, name: str):
        """
        Delete a secret (all versions).
        """
        self.client.secrets.kv.v2.delete_metadata_and_all_versions(
            path=f"projects/{project_id}/{name}",
            mount_point="secret"
        )

    def issue_sandbox_token(self, project_id: str, ttl: str = "30m") -> str:
        """
        Issue a token for the sandbox with read access to the project's secrets.
        """
        policy_name = f"project-{project_id}-read"
        
        # Ensure policy exists
        policy_rules = f"""
        path "secret/data/projects/{project_id}/*" {{
            capabilities = ["read"]
        }}
        """
        self.client.sys.create_or_update_policy(
            name=policy_name,
            policy=policy_rules
        )

        # Create token
        token = self.client.auth.token.create(
            policies=[policy_name],
            ttl=ttl
        )
        return token['auth']['client_token']
