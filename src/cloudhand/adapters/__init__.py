from __future__ import annotations

import abc
import json
import os
from typing import Dict, Type

import requests

from ..models import CloudGraph, Edge, EdgeType, Node, NodeType


class ProviderConfig(Dict[str, str]):
    """Simple provider config mapping (can evolve into a full model)."""


class CloudAdapter(abc.ABC):
    id: str

    @abc.abstractmethod
    def scan(self, config: ProviderConfig) -> CloudGraph:
        raise NotImplementedError


class HetznerAdapter(CloudAdapter):
    id = "hetzner"

    API_BASE = "https://api.hetzner.cloud/v1"

    def __init__(self) -> None:
        # A token can come either from HCLOUD_TOKEN or from ProviderConfig.
        self.token = os.getenv("HCLOUD_TOKEN") or None
        # Allow overriding the API endpoint (useful for tests/mocks).
        self.api_base = os.getenv("HCLOUD_ENDPOINT", self.API_BASE).rstrip("/")

    def _headers(self, config: ProviderConfig) -> Dict[str, str]:
        token = config.get("token") or self.token
        if not token:
            raise RuntimeError(
                "Hetzner token not configured. Set HCLOUD_TOKEN or "
                "configure a provider token via ch onboarding."
            )
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _get_paginated(self, path: str, root_key: str, headers: Dict[str, str]) -> list[dict]:
        """Fetch all pages for a list endpoint using meta.pagination.next_page."""
        items: list[dict] = []
        page = 1
        per_page = 50

        while True:
            resp = requests.get(
                f"{self.api_base}/{path}",
                headers=headers,
                params={"page": page, "per_page": per_page},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            chunk = data.get(root_key) or []
            if not isinstance(chunk, list):
                break
            items.extend(chunk)

            meta = data.get("meta") or {}
            pagination = meta.get("pagination") or {}
            next_page = pagination.get("next_page")
            if not next_page:
                break
            page = next_page

        return items

    def scan(self, config: ProviderConfig) -> CloudGraph:
        """Scan Hetzner Cloud and build a CloudGraph of core data-plane resources."""

        headers = self._headers(config)
        graph = CloudGraph()

        # Fetch all paginated resources we care about.
        servers_data = self._get_paginated("servers", "servers", headers)
        networks_data = self._get_paginated("networks", "networks", headers)
        volumes_data = self._get_paginated("volumes", "volumes", headers)
        lbs_data = self._get_paginated("load_balancers", "load_balancers", headers)
        fw_data = self._get_paginated("firewalls", "firewalls", headers)
        primary_ips_data = self._get_paginated("primary_ips", "primary_ips", headers)
        floating_ips_data = self._get_paginated("floating_ips", "floating_ips", headers)

        # Servers
        for s in servers_data:
            server_id = s["id"]
            node_id = f"hetzner:server:{server_id}"
            labels = s.get("labels") or {}

            public_net = s.get("public_net") or {}
            ipv4 = (public_net.get("ipv4") or {}).get("ip") or ""
            ipv6 = (public_net.get("ipv6") or {}).get("ip") or ""
            if not ipv4:
                ipv4 = public_net.get("ipv4_address") or ""

            private_ips: list[str] = []
            for nic in s.get("private_net", []):
                ip = nic.get("ip")
                if ip:
                    private_ips.append(ip)
                for alias in nic.get("alias_ips", []) or []:
                    if alias:
                        private_ips.append(alias)

            image_name = (s.get("image") or {}).get("name") or ""

            attrs = {
                "server_type": (s.get("server_type") or {}).get("name", ""),
                "datacenter": (s.get("datacenter") or {}).get("name", ""),
                "status": s.get("status", ""),
                "ipv4": ipv4,
                "ipv6": ipv6,
                "private_ips": ",".join(private_ips),
                "image": image_name,
                "created": s.get("created", ""),
                "raw": json.dumps(s),
            }

            graph.nodes.append(
                Node(
                    id=node_id,
                    type=NodeType.COMPUTE_INSTANCE,
                    name=s.get("name"),
                    region=(s.get("datacenter") or {}).get("location", {}).get("name"),
                    provider="hetzner",
                    provider_native_id=str(server_id),
                    labels=labels,
                    attrs=attrs,
                )
            )

        # Networks
        for n in networks_data:
            net_id = n["id"]
            node_id = f"hetzner:network:{net_id}"
            first_subnet = (n.get("subnets") or [{}])[0]

            graph.nodes.append(
                Node(
                    id=node_id,
                    type=NodeType.NETWORK,
                    name=n.get("name"),
                    region=first_subnet.get("network_zone"),
                    provider="hetzner",
                    provider_native_id=str(net_id),
                    labels=n.get("labels") or {},
                    attrs={
                        "ip_range": n.get("ip_range", ""),
                        "subnets": json.dumps(n.get("subnets", [])),
                        "routes": json.dumps(n.get("routes", [])),
                        "raw": json.dumps(n),
                    },
                )
            )

        # Link servers to networks via private_net attachments
        for s in servers_data:
            server_node_id = f"hetzner:server:{s['id']}"
            for nic in s.get("private_net", []):
                network_ref = nic.get("network")
                if network_ref is None:
                    continue
                net_node_id = f"hetzner:network:{network_ref}"
                graph.edges.append(
                    Edge(
                        **{
                            "from": server_node_id,
                            "to": net_node_id,
                            "type": EdgeType.IN_NETWORK,
                        }
                    )
                )

        # Volumes
        for v in volumes_data:
            vol_id = v["id"]
            node_id = f"hetzner:volume:{vol_id}"
            labels = v.get("labels") or {}
            location = (v.get("location") or {}).get("name") or ""
            region = (v.get("location") or {}).get("network_zone") or location

            attrs = {
                "size_gb": str(v.get("size", "")),
                "linux_device": v.get("linux_device", ""),
                "location": location,
                "created": v.get("created", ""),
                "raw": json.dumps(v),
            }

            graph.nodes.append(
                Node(
                    id=node_id,
                    type=NodeType.VOLUME,
                    name=v.get("name"),
                    region=region,
                    provider="hetzner",
                    provider_native_id=str(vol_id),
                    labels=labels,
                    attrs=attrs,
                )
            )

            server_ref = v.get("server")
            if server_ref:
                if isinstance(server_ref, dict):
                    server_id = server_ref.get("id")
                else:
                    server_id = server_ref
                if server_id is not None:
                    server_node_id = f"hetzner:server:{server_id}"
                    graph.edges.append(
                        Edge(
                            **{
                                "from": server_node_id,
                                "to": node_id,
                                "type": EdgeType.ATTACHED_TO,
                            }
                        )
                    )

        # Load balancers
        lb_target_ip_nodes: set[str] = set()

        for lb in lbs_data:
            lb_id = lb["id"]
            node_id = f"hetzner:lb:{lb_id}"
            labels = lb.get("labels") or {}

            lb_type = (lb.get("load_balancer_type") or {}).get("name") or ""
            public_net = lb.get("public_net") or {}
            ipv4 = (public_net.get("ipv4") or {}).get("ip") or ""
            ipv6 = (public_net.get("ipv6") or {}).get("ip") or ""

            services = lb.get("services") or []
            ports_desc = ", ".join(
                f"{s.get('listen_port')}->{s.get('destination_port')}/{s.get('protocol')}"
                for s in services
                if s.get("listen_port") and s.get("destination_port") and s.get("protocol")
            )

            attrs = {
                "lb_type": lb_type,
                "algorithm": (lb.get("algorithm") or {}).get("type", ""),
                "ipv4": ipv4,
                "ipv6": ipv6,
                "ports": ports_desc,
                "created": lb.get("created", ""),
                "raw": json.dumps(lb),
            }

            region = (lb.get("location") or {}).get("name")

            graph.nodes.append(
                Node(
                    id=node_id,
                    type=NodeType.LOAD_BALANCER,
                    name=lb.get("name"),
                    region=region,
                    provider="hetzner",
                    provider_native_id=str(lb_id),
                    labels=labels,
                    attrs=attrs,
                )
            )

            # Attach LB to private networks.
            for nic in lb.get("private_net", []) or []:
                net_ref = nic.get("network")
                if net_ref is None:
                    continue
                net_node_id = f"hetzner:network:{net_ref}"
                graph.edges.append(
                    Edge(
                        **{
                            "from": node_id,
                            "to": net_node_id,
                            "type": EdgeType.IN_NETWORK,
                        }
                    )
                )

            # Targets: link LB to servers or explicit IPs it fronts.
            for t in lb.get("targets", []) or []:
                t_type = t.get("type")

                if t_type == "server" and t.get("server"):
                    server_ref = t["server"]
                    if isinstance(server_ref, dict):
                        server_id = server_ref.get("id")
                    else:
                        server_id = server_ref
                    if server_id is not None:
                        server_node_id = f"hetzner:server:{server_id}"
                        graph.edges.append(
                            Edge(
                                **{
                                    "from": node_id,
                                    "to": server_node_id,
                                    "type": EdgeType.TARGETS,
                                }
                            )
                        )

                elif t_type == "ip" and t.get("ip"):
                    ip_info = t["ip"]
                    if isinstance(ip_info, dict):
                        target_ip = ip_info.get("ip")
                        use_private_ip = ip_info.get("use_private_ip", False)
                    else:
                        target_ip = ip_info
                        use_private_ip = False

                    if not target_ip:
                        continue

                    ip_node_id = f"hetzner:lb_target_ip:{lb_id}:{target_ip}"
                    if ip_node_id not in lb_target_ip_nodes:
                        lb_target_ip_nodes.add(ip_node_id)
                        graph.nodes.append(
                            Node(
                                id=ip_node_id,
                                type=NodeType.IP_ADDRESS,
                                name=target_ip,
                                region=region,
                                provider="hetzner",
                                provider_native_id=target_ip,
                                labels={},
                                attrs={
                                    "address": target_ip,
                                    "kind": "lb_target",
                                    "use_private_ip": str(use_private_ip),
                                },
                            )
                        )

                    graph.edges.append(
                        Edge(
                            **{
                                "from": node_id,
                                "to": ip_node_id,
                                "type": EdgeType.TARGETS,
                            }
                        )
                    )

        # Firewalls
        for fw in fw_data:
            fw_id = fw["id"]
            node_id = f"hetzner:firewall:{fw_id}"
            labels = fw.get("labels") or {}

            attrs = {
                "rules": json.dumps(fw.get("rules", [])),
                "applied_to": json.dumps(fw.get("applied_to", [])),
                "applied_to_resources": json.dumps(fw.get("applied_to_resources", [])),
                "created": fw.get("created", ""),
                "raw": json.dumps(fw),
            }

            graph.nodes.append(
                Node(
                    id=node_id,
                    type=NodeType.FIREWALL,
                    name=fw.get("name"),
                    provider="hetzner",
                    provider_native_id=str(fw_id),
                    labels=labels,
                    attrs=attrs,
                )
            )

            seen_server_ids: set[int] = set()

            def iter_targets():
                for t in fw.get("applied_to", []) or []:
                    yield t
                for t in fw.get("applied_to_resources", []) or []:
                    yield t

            for target in iter_targets():
                if target.get("type") != "server":
                    continue
                server_ref = target.get("server")
                if isinstance(server_ref, dict):
                    server_id = server_ref.get("id")
                else:
                    server_id = server_ref
                if not server_id or server_id in seen_server_ids:
                    continue
                seen_server_ids.add(server_id)
                server_node_id = f"hetzner:server:{server_id}"
                graph.edges.append(
                    Edge(
                        **{
                            "from": node_id,
                            "to": server_node_id,
                            "type": EdgeType.PROTECTED_BY,
                        }
                    )
                )

        # Primary IPs
        for ip in primary_ips_data:
            primary_id = ip["id"]
            node_id = f"hetzner:primary_ip:{primary_id}"

            attrs = {
                "address": ip.get("ip", ""),
                "assignee_type": ip.get("assignee_type", ""),
                "assignee_id": str(ip.get("assignee_id")) if ip.get("assignee_id") is not None else "",
                "created": ip.get("created", ""),
                "blocked": str(ip.get("blocked", "")),
                "auto_delete": str(ip.get("auto_delete", "")),
                "dns_ptr": json.dumps(ip.get("dns_ptr", {})),
                "raw": json.dumps(ip),
            }

            region = (ip.get("datacenter") or {}).get("location", {}).get("name")

            graph.nodes.append(
                Node(
                    id=node_id,
                    type=NodeType.IP_ADDRESS,
                    name=ip.get("ip"),
                    region=region,
                    provider="hetzner",
                    provider_native_id=str(primary_id),
                    labels=ip.get("labels") or {},
                    attrs=attrs,
                )
            )

            assignee_type = ip.get("assignee_type")
            assignee_id = ip.get("assignee_id")
            if assignee_type == "server" and assignee_id:
                target_node_id = f"hetzner:server:{assignee_id}"
                graph.edges.append(
                    Edge(
                        **{
                            "from": node_id,
                            "to": target_node_id,
                            "type": EdgeType.RESOLVES_TO,
                        }
                    )
                )

        # Floating IPs
        for ip in floating_ips_data:
            float_id = ip["id"]
            node_id = f"hetzner:floating_ip:{float_id}"
            home_loc = (ip.get("home_location") or {}).get("name")

            attrs = {
                "address": ip.get("ip", ""),
                "type": ip.get("type", ""),
                "home_location": home_loc or "",
                "server_id": str(ip.get("server")) if ip.get("server") is not None else "",
                "blocked": str(ip.get("blocked", "")),
                "dns_ptr": json.dumps(ip.get("dns_ptr", {})),
                "raw": json.dumps(ip),
            }

            graph.nodes.append(
                Node(
                    id=node_id,
                    type=NodeType.IP_ADDRESS,
                    name=ip.get("ip"),
                    region=home_loc,
                    provider="hetzner",
                    provider_native_id=str(float_id),
                    labels=ip.get("labels") or {},
                    attrs=attrs,
                )
            )

            server_id = ip.get("server")
            if server_id:
                server_node_id = f"hetzner:server:{server_id}"
                graph.edges.append(
                    Edge(
                        **{
                            "from": node_id,
                            "to": server_node_id,
                            "type": EdgeType.RESOLVES_TO,
                        }
                    )
                )

        return graph


ADAPTERS: Dict[str, Type[CloudAdapter]] = {
    HetznerAdapter.id: HetznerAdapter,
}


def get_adapter(provider: str) -> CloudAdapter:
    try:
        adapter_cls = ADAPTERS[provider]
    except KeyError as exc:
        raise ValueError(f"Unknown provider: {provider}") from exc
    return adapter_cls()
