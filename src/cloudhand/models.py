from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class NodeType(str, Enum):
    COMPUTE_INSTANCE = "ComputeInstance"
    LOAD_BALANCER = "LoadBalancer"
    NETWORK = "Network"
    SUBNET = "Subnet"
    FIREWALL = "Firewall"
    VOLUME = "Volume"
    IP_ADDRESS = "IpAddress"
    DNS_RECORD = "DnsRecord"


class EdgeType(str, Enum):
    ATTACHED_TO = "attached_to"
    IN_NETWORK = "in_network"
    PROTECTED_BY = "protected_by"
    TARGETS = "targets"
    RESOLVES_TO = "resolves_to"


class Node(BaseModel):
    id: str
    type: NodeType
    name: Optional[str] = None
    region: Optional[str] = None
    zone: Optional[str] = None
    provider: Optional[str] = None
    provider_native_id: Optional[str] = None
    labels: Dict[str, str] = Field(default_factory=dict)
    attrs: Dict[str, str] = Field(default_factory=dict)


class Edge(BaseModel):
    from_id: str = Field(alias="from")
    to_id: str = Field(alias="to")
    type: EdgeType

    class Config:
        populate_by_name = True


class CloudGraph(BaseModel):
    nodes: List[Node] = Field(default_factory=list)
    edges: List[Edge] = Field(default_factory=list)


class RuntimeType(str, Enum):
    DOCKER = "docker"
    NODEJS = "nodejs"
    PYTHON = "python"
    STATIC = "static"
    GO = "go"


class ServiceSpec(BaseModel):
    """How to run the app in the background."""

    command: str
    environment: Dict[str, str] = Field(default_factory=dict)
    environment_file: Optional[str] = None
    environment_file_upload: Optional[str] = None
    ports: List[int] = Field(default_factory=list)
    server_names: List[str] = Field(default_factory=list)
    https: bool = False


class BuildSpec(BaseModel):
    """How to build the app from source."""

    install_command: Optional[str] = None
    build_command: Optional[str] = None
    system_packages: List[str] = Field(default_factory=list)


class ApplicationSpec(BaseModel):
    name: str
    repo_url: str
    branch: str = "main"
    runtime: RuntimeType
    build_config: BuildSpec
    service_config: ServiceSpec
    destination_path: str = "/opt/apps"


class NetworkSpec(BaseModel):
    name: str
    cidr: str


class UnattendedUpgradesPolicy(BaseModel):
    enabled: bool = True
    allowed_origins: List[str] = Field(
        default_factory=lambda: [
            "${distro_id}:${distro_codename}-security",
            "${distro_id}ESMApps:${distro_codename}-apps-security",
            "${distro_id}ESM:${distro_codename}-infra-security",
        ]
    )
    auto_reboot: bool = False
    auto_reboot_time: str = "04:00"


class MaintenancePolicy(BaseModel):
    unattended_upgrades: UnattendedUpgradesPolicy = Field(default_factory=UnattendedUpgradesPolicy)


class InstanceSpec(BaseModel):
    name: str
    size: str
    network: str
    region: Optional[str] = None
    labels: Dict[str, str] = Field(default_factory=dict)
    workloads: List[ApplicationSpec] = Field(default_factory=list)
    maintenance: Optional[MaintenancePolicy] = None


class LoadBalancerPortSpec(BaseModel):
    port: int
    protocol: str


class LoadBalancerTargetSpec(BaseModel):
    type: str
    selector: str


class LoadBalancerSpec(BaseModel):
    name: str
    network: str
    ports: List[LoadBalancerPortSpec] = Field(default_factory=list)
    targets: List[LoadBalancerTargetSpec] = Field(default_factory=list)


class FirewallRuleSpec(BaseModel):
    direction: str
    protocol: str
    port: Optional[str] = None
    cidr: Optional[str] = None


class FirewallTargetSpec(BaseModel):
    type: str
    selector: str


class FirewallSpec(BaseModel):
    name: str
    rules: List[FirewallRuleSpec] = Field(default_factory=list)
    targets: List[FirewallTargetSpec] = Field(default_factory=list)


class DnsRecordSpec(BaseModel):
    zone: str
    name: str
    type: str
    target: str


class ContainerPortSpec(BaseModel):
    container_port: int
    host_port: Optional[int] = None


class ContainerVolumeSpec(BaseModel):
    host_path: str
    container_path: str


class ContainerEnvVar(BaseModel):
    name: str
    value: str


class ContainerSpec(BaseModel):
    name: str
    image: str
    host_selector: str
    ports: List[ContainerPortSpec] = Field(default_factory=list)
    env: List[ContainerEnvVar] = Field(default_factory=list)
    volumes: List[ContainerVolumeSpec] = Field(default_factory=list)
    restart_policy: str = "always"


class DesiredStateSpec(BaseModel):
    provider: str
    region: Optional[str] = None
    networks: List[NetworkSpec] = Field(default_factory=list)
    instances: List[InstanceSpec] = Field(default_factory=list)
    load_balancers: List[LoadBalancerSpec] = Field(default_factory=list)
    firewalls: List[FirewallSpec] = Field(default_factory=list)
    dns_records: List[DnsRecordSpec] = Field(default_factory=list)
    containers: List[ContainerSpec] = Field(default_factory=list)
