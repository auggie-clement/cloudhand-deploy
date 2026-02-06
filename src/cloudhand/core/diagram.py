import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import click  # Keeping click for exceptions, or should I use standard exceptions? Using standard for core.

from ..models import CloudGraph
from .paths import diagrams_dir, cloudhand_dir

def graph_to_mermaid(graph: CloudGraph) -> str:
    """Render a CloudGraph as a Mermaid flowchart.
    
    Design choices:
    - Group by VPC (if detectable), then by Network, then by cluster label.
    - Show servers with key attributes (type, region, pub/priv IP, role, attached volumes).
    - Summarise attached volumes inside the server node instead of separate nodes.
    - Only show edges that add information (targets, protected_by, etc.).
    """

    lines: list[str] = ["graph LR", ""]

    def safe_id(node_id: str) -> str:
        return (
            node_id.replace(":", "_")
            .replace("-", "_")
            .replace(".", "_")
        )

    def truncate(text: str, max_len: int = 60) -> str:
        if len(text) <= max_len:
            return text
        return text[: max_len - 3] + "..."

    def format_labels(labels: dict, omit: Optional[set] = None) -> str:
        if not labels:
            return ""
        omit = omit or set()
        items = [f"{k}={v}" for k, v in labels.items() if k not in omit]
        if not items:
            return ""
        joined = truncate(", ".join(items))
        return f"labels: {joined}"

    # Index nodes and edges
    nodes_by_id = {n.id: n for n in graph.nodes}

    networks = {n.id: n for n in graph.nodes if n.type.value == "Network"}
    servers = [n for n in graph.nodes if n.type.value == "ComputeInstance"]
    load_balancers = [n for n in graph.nodes if n.type.value == "LoadBalancer"]
    volumes = [n for n in graph.nodes if n.type.value == "Volume"]
    ip_addrs = [n for n in graph.nodes if n.type.value == "IpAddress"]
    firewalls = [n for n in graph.nodes if n.type.value == "Firewall"]

    # server_id -> [attached volumes...]
    attached_vol_ids: set[str] = set()
    server_vols: dict[str, list] = {}
    for e in graph.edges:
        if e.type.value == "attached_to":
            vol = nodes_by_id.get(e.to_id)
            if not vol or vol.type.value != "Volume":
                continue
            attached_vol_ids.add(vol.id)
            server_vols.setdefault(e.from_id, []).append(vol)

    unattached_volumes = [v for v in volumes if v.id not in attached_vol_ids]

    # IPs that resolve directly to servers; we won't render these as separate nodes
    ip_resolves_to_server: set[str] = set()
    for e in graph.edges:
        if e.type.value == "resolves_to":
            ip_resolves_to_server.add(e.from_id)

    loose_ips = [ip for ip in ip_addrs if ip.id not in ip_resolves_to_server]

    def build_volume_summary(vols: list) -> str:
        if not vols:
            return ""
        parts: list[str] = []
        for v in vols[:2]:
            size = v.attrs.get("size_gb") or "?"
            dev = v.attrs.get("linux_device") or ""
            if dev:
                parts.append(f"{size}GB @ {dev}")
            else:
                parts.append(f"{size}GB")
        if len(vols) > 2:
            parts.append(f"+{len(vols) - 2} more")
        return "; ".join(parts)

    def build_server_label(s) -> str:
        size = s.attrs.get("server_type") or ""
        region = s.region or ""
        role = s.labels.get("role")

        pub_v4 = s.attrs.get("ipv4") or ""
        priv_ips = s.attrs.get("private_ips") or ""

        label_lines: list[str] = [s.name or s.id]

        if size or region:
            label_lines.append(f"{size} @ {region}".strip())

        if role:
            label_lines.append(f"role: {role}")

        ip_parts: list[str] = []
        if pub_v4:
            ip_parts.append(f"pub: {pub_v4}")
        if priv_ips:
            ip_parts.append(f"priv: {priv_ips}")
        if ip_parts:
            label_lines.append("; ".join(ip_parts))

        vols = server_vols.get(s.id, [])
        vol_summary = build_volume_summary(vols)
        if vol_summary:
            label_lines.append(f"vol: {vol_summary}")

        labels_str = format_labels(s.labels, omit={"cluster", "role"})
        if labels_str:
            label_lines.append(labels_str)

        return "\n".join(label_lines)

    # Grouping: VPC -> Network -> Cluster
    vpc_candidates = [n for n in networks.values() if "vpc" in (n.name or "").lower()]
    vpc_net = vpc_candidates[0] if vpc_candidates else None

    def emit_network_block(net_id: str, indent: str = "  ") -> None:
        net = networks[net_id]
        net_label = net.name or net_id
        cidr = net.attrs.get("ip_range") or ""
        region = net.region or ""
        header = f"Network {net_label}"
        if cidr or region:
            header += f" ({cidr}{', ' if cidr and region else ''}{region})"

        lines.append(f'{indent}subgraph {safe_id(net_id)}["{header}"]')

        attached_servers = {
            e.from_id
            for e in graph.edges
            if e.type.value == "in_network" and e.to_id == net_id
        }
        servers_in_net = [s for s in servers if s.id in attached_servers]

        by_cluster: dict[str | None, list] = {}
        for s in servers_in_net:
            cluster_name = s.labels.get("cluster")
            by_cluster.setdefault(cluster_name, []).append(s)

        for cluster_name, cluster_servers in by_cluster.items():
            if cluster_name:
                cluster_id = f"cluster_{safe_id(cluster_name)}"
                cluster_title = f"Cluster {cluster_name}"
                lines.append(f'{indent}  subgraph {cluster_id}["{cluster_title}"]')
                node_indent = indent + "    "
            else:
                node_indent = indent + "  "

            def sort_key(s):
                return (s.labels.get("role") or "", s.name or s.id)

            for s in sorted(cluster_servers, key=sort_key):
                label = build_server_label(s)
                lines.append(f'{node_indent}{safe_id(s.id)}["{label}"]')

            if cluster_name:
                lines.append(f"{indent}  end")

        lines.append(f"{indent}end")
        lines.append("")

    if vpc_net:
        cidr = vpc_net.attrs.get("ip_range") or ""
        region = vpc_net.region or ""
        vpc_label = vpc_net.name or "vpc"
        header = f"VPC {vpc_label}"
        if cidr or region:
            header += f" ({cidr}{', ' if cidr and region else ''}{region})"
        vpc_id = f"vpc_{safe_id(vpc_net.id)}"
        lines.append(f'  subgraph {vpc_id}["{header}"]')

        for net_id in networks:
            if net_id == vpc_net.id:
                continue
            emit_network_block(net_id, indent="    ")

        lines.append("  end")
        lines.append("")
    else:
        if networks:
            for net_id in networks:
                emit_network_block(net_id)
        else:
            lines.append('  subgraph default_network["Network (unattached)"]')
            for s in servers:
                label = build_server_label(s)
                lines.append(f'    {safe_id(s.id)}["{label}"]')
            lines.append("  end")
            lines.append("")

    # Load balancers
    for lb in load_balancers:
        ports = lb.attrs.get("ports") or ""
        region = lb.region or ""
        lb_type = lb.attrs.get("lb_type") or ""
        ipv4 = lb.attrs.get("ipv4") or ""
        ipv6 = lb.attrs.get("ipv6") or ""

        label_lines = [
            lb.name or lb.id,
            f"{lb_type} @ {region}".strip(),
        ]
        if ports:
            label_lines.append(ports)

        ip_parts: list[str] = []
        if ipv4:
            ip_parts.append(ipv4)
        if ipv6:
            ip_parts.append(ipv6)
        if ip_parts:
            label_lines.append(" / ".join(ip_parts))

        label = "\n".join(label_lines)
        lines.append(f'  {safe_id(lb.id)}{{"{label}"}}')

    # Unattached volumes
    for v in unattached_volumes:
        size = v.attrs.get("size_gb") or ""
        device = v.attrs.get("linux_device") or ""
        region = v.region or ""
        label = f"{v.name or v.id}\\n{size} GB @ {region}".strip()
        if device:
            label += f"\\n{device}"
        lines.append(f'  {safe_id(v.id)}[("{label}")]')

    # IP addresses (only those not already on servers)
    for ip in loose_ips:
        addr = ip.attrs.get("address") or ip.name or ip.id
        lines.append(f'  {safe_id(ip.id)}(("IP {addr}"))')

    # Firewalls
    for fw in firewalls:
        labels_str = format_labels(fw.labels)
        label_lines = [fw.name or fw.id]
        if labels_str:
            label_lines.append(labels_str)
        fw_label = "\n".join(label_lines)
        lines.append(f'  {safe_id(fw.id)}["FW {fw_label}"]')

    # Edges: only ones that add information
    IGNORED_EDGE_TYPES = {"in_network", "attached_to", "resolves_to"}
    for e in graph.edges:
        if e.type.value in IGNORED_EDGE_TYPES:
            continue
        from_node = safe_id(e.from_id)
        to_node = safe_id(e.to_id)
        label = e.type.value
        lines.append(f"  {from_node} --{label}--> {to_node}")

    # Styling
    lines.append("")
    lines.append("  classDef server stroke-width:1px;")
    lines.append("  classDef lb stroke-width:2px;")
    lines.append("  classDef firewall stroke-dasharray:3 3;")
    lines.append("  classDef volume stroke-dasharray:2 2;")
    for s in servers:
        lines.append(f"  class {safe_id(s.id)} server;")
    for lb in load_balancers:
        lines.append(f"  class {safe_id(lb.id)} lb;")
    for fw in firewalls:
        lines.append(f"  class {safe_id(fw.id)} firewall;")
    for v in unattached_volumes:
        lines.append(f"  class {safe_id(v.id)} volume;")

    # Legend
    lines.append("")
    lines.append('  subgraph legend["Legend"]')
    lines.append('    legend_server["Server (Compute)"]')
    lines.append('    legend_lb{{"Load Balancer"}}')
    lines.append('    legend_fw["Firewall"]')
    lines.append('    legend_vol[("Volume")]')
    lines.append('    legend_ip(("Public/Primary IP"))')
    lines.append("  end")
    lines.append("  class legend_server server;")
    lines.append("  class legend_lb lb;")
    lines.append("  class legend_fw firewall;")
    lines.append("  class legend_vol volume;")

    return "\n".join(lines) + "\n"

def graph_to_mermaid_via_llm(graph: CloudGraph) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "OPENAI_API_KEY is not set; cannot generate diagram via OpenAI. "
            "Set OPENAI_API_KEY in your environment."
        )

    # Default to a reasoning-capable GPT-5.1 model with high effort.
    model = os.getenv("OPENAI_DIAGRAM_MODEL", "gpt-5.1")

    import requests as _requests

    system_prompt = (
        "You are an expert in cloud infrastructure diagrams. "
        "You are given a provider-agnostic CloudGraph JSON with nodes and edges. "
        "Produce a Mermaid graph definition (flowchart syntax, direction LR) that clearly shows "
        "networks, compute instances, and their relationships. "
        "Only output the Mermaid code; do not include explanations or backticks."
    )

    input_payload = {
        "graph_json": graph.model_dump(),
        "requirements": "Mermaid graph LR, focus on networks and compute, no prose.",
    }

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
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()

        # Prefer aggregated text if available.
        content = data.get("output_text") or ""
        if not content:
            parts: list[str] = []
            for item in data.get("output", []):
                for c in item.get("content", []):
                    if c.get("type") == "output_text":
                        parts.append(c.get("text", ""))
            content = "".join(parts)
    except Exception as exc:
        raise ValueError(f"Failed to generate diagram via OpenAI: {exc}") from exc

    # Strip Markdown fences if the model added them anyway.
    if "```" in content:
        parts = content.split("```")
        # Take the first non-empty part that isn't a language identifier.
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if part.lower().startswith("mermaid"):
                part = part[len("mermaid") :].lstrip()
            if part:
                content = part
                break

    return content.strip() + "\n"

def generate_diagram(root: Path, use_llm: bool = True, output_path: Optional[Path] = None) -> Path:
    scan_path = cloudhand_dir(root) / "scan.json"
    if not scan_path.exists():
        raise FileNotFoundError(f"scan.json not found at {scan_path}. Run 'ch scan' first.")

    data = json.loads(scan_path.read_text(encoding="utf-8"))
    graph = CloudGraph.model_validate(data)

    if use_llm and os.getenv("OPENAI_API_KEY"):
        try:
            mmd = graph_to_mermaid_via_llm(graph)
        except ValueError as exc:
            # Log warning? For now just fall back
            mmd = graph_to_mermaid(graph)
    else:
        mmd = graph_to_mermaid(graph)

    diag_dir = diagrams_dir(root)
    diag_dir.mkdir(parents=True, exist_ok=True)
    if output_path is None:
        output_path = diag_dir / "current.mmd"
    
    output_path.write_text(mmd, encoding="utf-8")

    # Save history copy with timestamp
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%SZ")
    history_path = diag_dir / "history" / f"{ts}.mmd"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(mmd, encoding="utf-8")

    return output_path
