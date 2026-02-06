import json
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

# Import from cloudhand
try:
    from cloudhand.models import CloudGraph, NodeType
except ImportError:
    # Fallback for when cloudhand is not yet in path (during dev/linting)
    pass

from services import onboarding as onboarding_service

router = APIRouter()

class Resource(BaseModel):
    id: str
    name: str
    type: str
    status: str
    specs: str
    ip: Optional[str] = None
    uptime: Optional[str] = None
    region: Optional[str] = None
    os: Optional[str] = None
    kernel: Optional[str] = None
    image: Optional[str] = None

def get_cloudhand_dir() -> Path:
    # /.../cloudhand/cloudhand-api/src/routers/resources.py
    # -> /.../cloudhand/cloudhand
    return Path(__file__).parent.parent.parent.parent / "cloudhand"

@router.get("/", response_model=List[Resource])
async def get_resources(refresh: bool = Query(False, description="Re-scan provider before returning resources")):
    graph = None

    if refresh:
        status = onboarding_service.onboarding_status()
        provider = status.get("provider")
        if not provider:
            raise HTTPException(status_code=400, detail="Provider not configured")

        cfg = onboarding_service.load_provider_config(provider)
        token = cfg.get("token")
        if not token:
            raise HTTPException(status_code=400, detail="Provider token missing")

        try:
            graph = onboarding_service.run_scan(provider, token)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to run scan: {exc}") from exc

    if graph is None:
        scan_path = get_cloudhand_dir() / "scan.json"
        if not scan_path.exists():
            return []

        try:
            data = json.loads(scan_path.read_text())
            graph = CloudGraph.model_validate(data)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to load scan data: {e}")

    resources = []
    
    for node in graph.nodes:
        if node.type == NodeType.COMPUTE_INSTANCE:
            # Map ComputeInstance to Resource
            specs = f"{node.attrs.get('server_type', 'Unknown')} • {node.attrs.get('cores', '?')} vCPU"
            ip = node.attrs.get('ipv4') or node.attrs.get('private_ips')
            
            resources.append(Resource(
                id=node.id,
                name=node.name or node.id,
                type="server",
                status=node.attrs.get('status', 'running'), # Default to running if not present
                specs=specs,
                ip=ip,
                region=node.region,
                os=node.attrs.get('image'),
                kernel="N/A", # Not usually in scan
                image=node.attrs.get('image')
            ))
        elif node.type == NodeType.LOAD_BALANCER:
             resources.append(Resource(
                id=node.id,
                name=node.name or node.id,
                type="loadbalancer",
                status="running",
                specs=node.attrs.get('lb_type', 'LB'),
                ip=node.attrs.get('ipv4'),
                region=node.region,
            ))
        # Add other types as needed (Database, Container)

    return resources
