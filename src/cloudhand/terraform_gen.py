from __future__ import annotations

from pathlib import Path

from .models import DesiredStateSpec, InstanceSpec

DEFAULT_ALLOWED_ORIGINS = [
    "${distro_id}:${distro_codename}-security",
    "${distro_id}ESMApps:${distro_codename}-apps-security",
    "${distro_id}ESM:${distro_codename}-infra-security",
]
DEFAULT_REBOOT_TIME = "04:00"


class TerraformGenerator:
    id: str

    def generate(
        self,
        spec: DesiredStateSpec,
        out_dir: Path,
        project_id: str = "default",
        workspace_id: str = "default",
    ) -> None:
        raise NotImplementedError


class HetznerTerraformGenerator(TerraformGenerator):
    id = "hetzner"

    @staticmethod
    def _base_user_data() -> str:
        """Cloud-init baseline: packages + unattended security updates."""
        return """#cloud-config
package_update: true
package_upgrade: true
packages:
  - git
  - curl
  - python3
  - python3-pip
  - nginx
  - docker.io
  - unattended-upgrades

ssh_pwauth: false
chpasswd:
  expire: False
  list: |
    root:cloudhand-temp-rotate
users:
  - name: root
    ssh_authorized_keys:
      - ${var.ssh_public_key}

write_files:
  - path: /etc/apt/apt.conf.d/20auto-upgrades
    permissions: "0644"
    content: |
      APT::Periodic::Update-Package-Lists "__CH_PERIODIC_UPDATE__";
      APT::Periodic::Unattended-Upgrade "__CH_PERIODIC_UPGRADE__";

  - path: /etc/apt/apt.conf.d/50unattended-upgrades
    permissions: "0644"
    content: |
      Unattended-Upgrade::Allowed-Origins {
__CH_ALLOWED_ORIGINS__
      };

      Unattended-Upgrade::Automatic-Reboot "__CH_AUTO_REBOOT__";
      Unattended-Upgrade::Automatic-Reboot-Time "__CH_REBOOT_TIME__";

  - path: /etc/systemd/system/apt-daily.timer.d/override.conf
    permissions: "0644"
    content: |
      [Timer]
      OnCalendar=
      OnCalendar=*-*-* 02:00:00
      RandomizedDelaySec=15m
      Persistent=false

  - path: /etc/systemd/system/apt-daily-upgrade.timer.d/override.conf
    permissions: "0644"
    content: |
      [Timer]
      OnCalendar=
      OnCalendar=*-*-* 02:15:00
      RandomizedDelaySec=15m
      Persistent=false

runcmd:
  - bash -lc "chage -I -1 -m 0 -M 99999 -E -1 root"
  - bash -lc "chage -d $(date +%Y-%m-%d) root"
  - passwd -d root || true
  - systemctl daemon-reload
  - systemctl restart apt-daily.timer apt-daily-upgrade.timer
  - systemctl enable nginx
  - systemctl start nginx
"""

    def _render_allowed_origins(self, origins: list[str]) -> str:
        """Render Allowed-Origins with Terraform-safe escaping for ${...}."""
        escaped = [origin.replace("${", "$${") for origin in origins]
        return "".join(f'          "{origin}";\n' for origin in escaped)

    def _user_data_for_instance(self, inst: InstanceSpec) -> str:
        """Adjust baseline unattended-upgrades policy per instance."""
        labels = inst.labels or {}
        role = (labels.get("role") or "").lower()
        stateful = labels.get("stateful", "").lower() == "true" or role in {"db", "postgres"}

        policy = inst.maintenance.unattended_upgrades if inst.maintenance else None

        allowed_origins = DEFAULT_ALLOWED_ORIGINS
        if policy and policy.allowed_origins:
            allowed_origins = policy.allowed_origins

        auto_reboot_value = not stateful
        reboot_time = DEFAULT_REBOOT_TIME
        if policy is not None:
            auto_reboot_value = policy.auto_reboot
            reboot_time = policy.auto_reboot_time or DEFAULT_REBOOT_TIME

        periodic_update = "1"
        periodic_upgrade = "1"
        if policy is not None and not policy.enabled:
            periodic_update = "0"
            periodic_upgrade = "0"

        return (
            self._base_user_data()
            .replace("__CH_ALLOWED_ORIGINS__", self._render_allowed_origins(allowed_origins))
            .replace("__CH_AUTO_REBOOT__", "true" if auto_reboot_value else "false")
            .replace("__CH_REBOOT_TIME__", reboot_time)
            .replace("__CH_PERIODIC_UPDATE__", periodic_update)
            .replace("__CH_PERIODIC_UPGRADE__", periodic_upgrade)
        )

    def _server_block(self, inst: InstanceSpec, spec: DesiredStateSpec, user_data: str) -> str:
        res_name = inst.name.replace("-", "_")
        net_name = inst.network.replace("-", "_")
        labels_hcl = "".join(f'    {k} = "{v}"\n' for k, v in (inst.labels or {}).items())
        user_data_hcl = "  user_data = <<-EOF\n" + user_data + "EOF\n"

        block: list[str] = []
        block.append(f'resource "hcloud_server" "{res_name}" {{\n')
        block.append(f'  name        = "{inst.name}"\n')
        block.append(f'  server_type = "{inst.size}"\n')
        block.append('  image       = "ubuntu-22.04"\n')
        if inst.region or spec.region:
            block.append(f'  location    = "{inst.region or spec.region}"\n')
        if labels_hcl:
            block.append("  labels = {\n" + labels_hcl + "  }\n")
        block.append("\n  network {\n")
        block.append(f"    network_id = data.hcloud_network.{net_name}.id\n")
        block.append("  }\n\n")
        block.append(user_data_hcl)
        block.append("  lifecycle {\n")
        block.append("    ignore_changes = [user_data]\n")
        block.append("  }\n")
        block.append("}\n\n")
        return "".join(block)

    def generate(
        self,
        spec: DesiredStateSpec,
        out_dir: Path,
        project_id: str = "default",
        workspace_id: str = "default",
    ) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)

        (out_dir / "backend.tf").write_text(
            """terraform {
  # Using local backend for now; swap to remote backend when available.
}
""",
            encoding="utf-8",
        )

        (out_dir / "providers.tf").write_text(
            """terraform {
  required_providers {
    hcloud = {
      source  = "hetznercloud/hcloud"
      version = "~> 1.0"
    }
  }
}

provider "hcloud" {
  token = var.hcloud_token
}
""",
            encoding="utf-8",
        )

        (out_dir / "variables.tf").write_text(
            """variable "hcloud_token" {
  type      = string
  sensitive = true
}

variable "ssh_public_key" {
  type = string
}
""",
            encoding="utf-8",
        )

        network_blocks: list[str] = []
        for net in spec.networks:
            res_name = net.name.replace("-", "_")
            network_blocks.append(
                f'data "hcloud_network" "{res_name}" {{\n'
                f'  name = "{net.name}"\n'
                f"}}\n\n"
            )
        (out_dir / "network.tf").write_text("".join(network_blocks), encoding="utf-8")

        server_blocks: list[str] = []
        for inst in spec.instances:
            user_data = self._user_data_for_instance(inst)
            server_blocks.append(self._server_block(inst, spec, user_data))
        (out_dir / "servers.tf").write_text("".join(server_blocks), encoding="utf-8")

        outputs: list[str] = []
        outputs.append('output "server_ips" {\n  value = {\n')
        for inst in spec.instances:
            res_name = inst.name.replace("-", "_")
            outputs.append(f'    "{inst.name}" = hcloud_server.{res_name}.ipv4_address\n')
        outputs.append("  }\n}\n")
        (out_dir / "outputs.tf").write_text("".join(outputs), encoding="utf-8")


GENERATOR_BY_PROVIDER = {
    HetznerTerraformGenerator.id: HetznerTerraformGenerator(),
}


def get_generator(provider: str) -> TerraformGenerator:
    try:
        return GENERATOR_BY_PROVIDER[provider]
    except KeyError as exc:
        raise ValueError(f"Unknown provider: {provider}") from exc
