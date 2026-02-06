from pathlib import Path
from ..adapters import ProviderConfig, get_adapter
from ..models import CloudGraph
from .paths import cloudhand_dir

def run_scan(
    root: Path,
    provider: str,
    provider_config: ProviderConfig,
) -> CloudGraph:
    print(f"Scanning infrastructure for provider: {provider}...")
    adapter = get_adapter(provider)
    graph = adapter.scan(provider_config)
    print(f"Scan complete. Found {len(graph.nodes)} resources.")
    
    ch_dir = cloudhand_dir(root)
    ch_dir.mkdir(parents=True, exist_ok=True)
    out_path = ch_dir / "scan.json"
    out_path.write_text(graph.model_dump_json(indent=2), encoding="utf-8")
    print(f"Written scan results to {out_path}")
    return graph
