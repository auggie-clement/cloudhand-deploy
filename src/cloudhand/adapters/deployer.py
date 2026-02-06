import io
import os
import time
from pathlib import Path
import paramiko
from typing import Dict, List, Optional  # noqa: F401

from ..models import ApplicationSpec, RuntimeType


class ServerDeployer:
    """SSH-based deployer that configures apps without replacing servers."""

    def __init__(
        self,
        ip: str,
        private_key_str: str,
        user: str = "root",
        local_root: Optional[Path] = None,
    ):
        self.ip = ip
        self.user = user
        self.key = paramiko.RSAKey.from_private_key(io.StringIO(private_key_str))
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.local_root = Path(local_root).expanduser().resolve() if local_root else None

    def connect(self):
        for _ in range(10):
            try:
                self.client.connect(self.ip, username=self.user, pkey=self.key, timeout=10)
                return
            except Exception:
                time.sleep(5)
        raise ConnectionError(f"Could not connect to {self.ip}")

    def run(self, cmd: str, cwd: str = None, mask: Optional[List[str]] = None) -> str:
        if self.client.get_transport() is None or not self.client.get_transport().is_active():
            self.connect()

        final_cmd = f"cd {cwd} && {cmd}" if cwd else cmd
        display_cmd = final_cmd
        for m in mask or []:
            if m:
                display_cmd = display_cmd.replace(m, "***")
        print(f"[{self.ip}] Running: {display_cmd}")
        stdin, stdout, stderr = self.client.exec_command(final_cmd)

        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode().strip()
        err = stderr.read().decode().strip()

        if exit_code != 0:
            raise Exception(f"Command failed: {final_cmd}\nError: {err}")
        return out

    def upload_file(self, content: str, remote_path: str):
        if self.client.get_transport() is None or not self.client.get_transport().is_active():
            self.connect()
        sftp = self.client.open_sftp()
        with sftp.file(remote_path, "w") as f:
            f.write(content)
        sftp.close()

    def _normalize_server_names(self, server_names: Optional[List[str]]) -> List[str]:
        names: List[str] = []
        for name in server_names or []:
            cleaned = (name or "").strip()
            if cleaned and cleaned not in names:
                names.append(cleaned)
        return names

    def _server_name_directive(self, server_names: List[str]) -> str:
        return " ".join(server_names) if server_names else "_"

    def _resolve_local_path(self, raw_path: str) -> Path:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            base = self.local_root or Path.cwd()
            path = base / path
        return path.resolve()

    def _env_lines_from_map(self, env_map: Dict[str, str]) -> List[str]:
        lines: List[str] = []
        for key, value in env_map.items():
            if value is None:
                continue
            lines.append(f"{key}={str(value).strip()}")
        return lines

    def _upload_env_content(self, content: str, remote_path: str) -> None:
        remote_dir = os.path.dirname(remote_path)
        if remote_dir:
            self.run(f"mkdir -p {remote_dir}")
        self.upload_file(content, remote_path)
        self.run(f"chmod 600 {remote_path}")

    def _write_env_file(self, app: ApplicationSpec, path: Optional[str] = None) -> Optional[str]:
        lines = self._env_lines_from_map(app.service_config.environment)
        if not lines:
            return None

        target = path or f"/etc/cloudhand/env/{app.name}.env"
        self._upload_env_content("\n".join(lines) + "\n", target)
        return target

    def _parse_env_file_path(self, raw_path: str, app_dir: str) -> Optional[tuple[str, bool]]:
        cleaned = (raw_path or "").strip()
        if not cleaned:
            return None
        optional = cleaned.startswith("-")
        if optional:
            cleaned = cleaned[1:]
        if cleaned.startswith("/"):
            resolved = cleaned
        else:
            resolved = f"{app_dir}/{cleaned}"
        return resolved, optional

    def _resolve_env_file(self, raw_path: str, app_dir: str) -> Optional[str]:
        parsed = self._parse_env_file_path(raw_path, app_dir)
        if not parsed:
            return None
        resolved, optional = parsed
        return f"-{resolved}" if optional else resolved

    def _env_file_directives(self, app: ApplicationSpec, app_dir: str) -> str:
        env_files: List[str] = []
        env_map_consumed = False

        if app.service_config.environment_file_upload:
            local_path = self._resolve_local_path(app.service_config.environment_file_upload)
            if not local_path.exists():
                raise FileNotFoundError(f"Environment file not found at {local_path}")
            content = local_path.read_text(encoding="utf-8")
            if not content.endswith("\n"):
                content += "\n"

            extra_lines = self._env_lines_from_map(app.service_config.environment)
            if extra_lines:
                content += "\n".join(extra_lines) + "\n"
                env_map_consumed = True

            target_raw = app.service_config.environment_file or f"/etc/cloudhand/env/{app.name}.env"
            parsed = self._parse_env_file_path(target_raw, app_dir)
            if not parsed:
                raise ValueError("environment_file_upload set but no target path resolved")
            target_path, optional = parsed
            self._upload_env_content(content, target_path)
            env_files.append(f"-{target_path}" if optional else target_path)
        elif app.service_config.environment_file:
            resolved = self._resolve_env_file(app.service_config.environment_file, app_dir)
            if resolved:
                env_files.append(resolved)

        if app.service_config.environment and not env_map_consumed:
            generated = self._write_env_file(app)
            if generated:
                env_files.append(generated)

        if not env_files:
            return ""

        return "\n".join(f"EnvironmentFile={path}" for path in env_files)

    def _configure_systemd(self, app: ApplicationSpec, app_dir: str):
        env_str = self._env_file_directives(app, app_dir)
        unit = f"""[Unit]
Description={app.name}
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory={app_dir}
ExecStart={app.service_config.command}
Restart=always
{env_str}

[Install]
WantedBy=multi-user.target
"""
        self.upload_file(unit, f"/etc/systemd/system/{app.name}.service")
        self.run("systemctl daemon-reload")
        self.run(f"systemctl enable {app.name}")
        self.run(f"systemctl restart {app.name}")

    def _enable_https(self, server_names: List[str]):
        names = self._normalize_server_names(server_names)
        if not names:
            print(f"[{self.ip}] HTTPS requested but no server_names configured; skipping certificate setup.")
            return

        domain_args = " ".join(f"-d {name}" for name in names)
        cert_cmd = self.run("command -v certbox || command -v certbot || true").strip()
        if not cert_cmd:
            self.run(
                "DEBIAN_FRONTEND=noninteractive apt-get update && "
                "DEBIAN_FRONTEND=noninteractive apt-get install -y certbot python3-certbot-nginx"
            )
            cert_cmd = "certbot"

        cmd_name = os.path.basename(cert_cmd)
        if cmd_name == "certbot":
            email = os.getenv("CERTBOT_EMAIL") or os.getenv("LETSENCRYPT_EMAIL") or ""
            email_flag = f"--email {email}" if email else "--register-unsafely-without-email"
            self.run(
                f"{cert_cmd} --nginx {domain_args} --non-interactive --agree-tos {email_flag} --redirect"
            )
        else:
            # Assume certbox is certbot-compatible.
            self.run(
                f"{cert_cmd} --nginx {domain_args} --non-interactive --agree-tos --redirect "
                "--register-unsafely-without-email"
            )

    def _configure_nginx(self, app: ApplicationSpec):
        if not app.service_config.ports:
            return

        # Ensure nginx exists (Hetzner cloud-init installs it in our Terraform, but be defensive)
        nginx_bin = self.run("command -v nginx || true").strip()
        if not nginx_bin:
            self.run(
                "DEBIAN_FRONTEND=noninteractive apt-get update && "
                "DEBIAN_FRONTEND=noninteractive apt-get install -y nginx"
            )

        self.run("mkdir -p /etc/nginx/sites-available /etc/nginx/sites-enabled")
        self.run("systemctl enable nginx || true")
        self.run("systemctl start nginx || true")

        port = app.service_config.ports[0]
        server_names = self._normalize_server_names(app.service_config.server_names)
        server_name_line = self._server_name_directive(server_names)
        conf = f"""server {{
    listen 80;
    server_name {server_name_line};
    location / {{
        proxy_pass http://127.0.0.1:{port};
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
    }}
}}"""
        self.upload_file(conf, f"/etc/nginx/sites-available/{app.name}")
        self.run(f"ln -sf /etc/nginx/sites-available/{app.name} /etc/nginx/sites-enabled/{app.name}")
        self.run("rm -f /etc/nginx/sites-enabled/default")
        self.run("systemctl reload nginx")
        if app.service_config.https:
            self._enable_https(server_names)

    def configure_combined_nginx(self, apps: List[ApplicationSpec]):
        """Create a single site config that proxies UI root and API paths on one host."""
        candidates = [a for a in apps if a.service_config.ports]
        if not candidates:
            return

        # Prefer an app named *ui* as the root site; otherwise first app.
        root_app = next((a for a in candidates if "ui" in a.name.lower()), candidates[0])
        root_port = root_app.service_config.ports[0]

        locations = []
        for app in candidates:
            port = app.service_config.ports[0]
            if app is root_app:
                continue
            name = app.name.lower()
            prefix = "/api/" if "api" in name else f"/{app.name}/"
            if not prefix.startswith("/"):
                prefix = f"/{prefix}"
            if not prefix.endswith("/"):
                prefix = f"{prefix}/"
            locations.append(
                f"""    location {prefix} {{
        proxy_pass http://127.0.0.1:{port}/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }}"""
            )

        server_names: List[str] = []
        https_enabled = False
        for app in candidates:
            server_names.extend(app.service_config.server_names)
            https_enabled = https_enabled or app.service_config.https

        server_names = self._normalize_server_names(server_names)
        server_name_line = self._server_name_directive(server_names)

        config = [
            "server {",
            "    listen 80;",
            f"    server_name {server_name_line};",
            "    client_max_body_size 25m;",
            "    location / {",
            f"        proxy_pass http://127.0.0.1:{root_port}/;",
            "        proxy_http_version 1.1;",
            "        proxy_set_header Upgrade $http_upgrade;",
            "        proxy_set_header Connection 'upgrade';",
            "        proxy_set_header Host $host;",
            "        proxy_set_header X-Real-IP $remote_addr;",
            "    }",
        ]
        config.extend(locations)
        config.append("}")
        conf = "\n".join(config)
        self.upload_file(conf, "/etc/nginx/sites-available/cloudhand")
        self.run("ln -sf /etc/nginx/sites-available/cloudhand /etc/nginx/sites-enabled/cloudhand")
        self.run("rm -f /etc/nginx/sites-enabled/default || true")
        self.run("systemctl reload nginx")
        if https_enabled:
            self._enable_https(server_names)

    def deploy(self, app: ApplicationSpec, configure_nginx: bool = True):
        app_dir = f"{app.destination_path}/{app.name}"

        # 1. System Deps
        if app.build_config.system_packages:
            self.run(
                f"DEBIAN_FRONTEND=noninteractive apt-get update && "
                f"DEBIAN_FRONTEND=noninteractive apt-get install -y {' '.join(app.build_config.system_packages)}"
            )
        if app.runtime == RuntimeType.NODEJS:
            # Ensure a modern Node runtime regardless of base image defaults.
            self.run(
                "DEBIAN_FRONTEND=noninteractive apt-get purge -y nodejs npm libnode-dev libnode72 || true"
            )
            self.run("DEBIAN_FRONTEND=noninteractive apt-get autoremove -y || true")
            self.run('bash -lc "curl -fsSL https://deb.nodesource.com/setup_20.x | bash -"')
            self.run("DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs")

        # 2. Git Sync
        gh_token = (
            os.getenv("GITHUB_TOKEN")
            or os.getenv("GH_TOKEN")
            or os.getenv("GITHUB_PAT")
            or ""
        )
        try:
            self.run(f"test -d {app_dir}")
            fetch_cmd = f"git fetch origin && git reset --hard origin/{app.branch}"
            if gh_token:
                fetch_cmd = (
                    f'git -c http.extraheader="Authorization: Bearer {gh_token}" '
                    f'fetch origin && git -c http.extraheader="Authorization: Bearer {gh_token}" '
                    f"reset --hard origin/{app.branch}"
                )
            self.run(fetch_cmd, cwd=app_dir, mask=[gh_token] if gh_token else None)
        except Exception:
            # Clean any partial checkout and reclone.
            self.run(f"rm -rf {app_dir}")
            self.run(f"mkdir -p {app.destination_path}")
            clone_url = app.repo_url
            if gh_token:
                clone_url = app.repo_url.replace("https://", f"https://{gh_token}@")
            self.run(
                f"git clone --branch {app.branch} {clone_url} {app_dir}",
                mask=[gh_token] if gh_token else None,
            )

        # 3. Build
        if app.build_config.install_command:
            self.run(app.build_config.install_command, cwd=app_dir)
        if app.build_config.build_command:
            self.run(app.build_config.build_command, cwd=app_dir)

        # 4. Services
        self._configure_systemd(app, app_dir)
        if configure_nginx:
            self._configure_nginx(app)
