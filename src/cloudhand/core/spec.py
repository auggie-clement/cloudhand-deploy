import json
from pathlib import Path
from ..models import CloudGraph, DesiredStateSpec, NetworkSpec, InstanceSpec
from .paths import cloudhand_dir

def graph_to_spec(graph: CloudGraph, provider: str) -> DesiredStateSpec:
    networks: dict[str, NetworkSpec] = {}
    instances: list[InstanceSpec] = []

    # Build networks
    for node in graph.nodes:
        if node.type.value == "Network":
            cidr = node.attrs.get("ip_range") or "10.0.0.0/24"
            name = node.name or node.id
            networks[node.id] = NetworkSpec(name=name, cidr=cidr)

    # Map server -> network name via edges
    server_network: dict[str, str] = {}
    for edge in graph.edges:
        if edge.type.value == "in_network" and edge.to_id in networks:
            server_network[edge.from_id] = networks[edge.to_id].name

    regions: set[str] = set()

    for node in graph.nodes:
        if node.type.value == "ComputeInstance":
            net_name = server_network.get(node.id, "default")
            if net_name not in {n.name for n in networks.values()}:
                # Ensure network exists if referenced
                networks.setdefault(
                    f"synthetic:{net_name}",
                    NetworkSpec(name=net_name, cidr="10.0.0.0/24"),
                )
            size = node.attrs.get("server_type") or "cx21"
            inst_region = node.region
            if inst_region:
                regions.add(inst_region)
            instances.append(
                InstanceSpec(
                    name=node.name or node.id,
                    size=size,
                    network=net_name,
                    region=inst_region,
                    labels=node.labels,
                )
            )

    region = next(iter(regions)) if regions else None

    return DesiredStateSpec(
        provider=provider,
        region=region,
        networks=list(networks.values()),
        instances=instances,
    )

def sync_spec(root: Path, provider: str) -> DesiredStateSpec:
    scan_path = cloudhand_dir(root) / "scan.json"
    if not scan_path.exists():
        raise FileNotFoundError(f"scan.json not found at {scan_path}. Run 'ch scan' first.")

    data = json.loads(scan_path.read_text(encoding="utf-8"))
    graph = CloudGraph.model_validate(data)

    spec = graph_to_spec(graph, provider=provider)

    ch_dir = cloudhand_dir(root)
    ch_dir.mkdir(parents=True, exist_ok=True)
    spec_path = ch_dir / "spec.json"
    spec_path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")

    return spec
