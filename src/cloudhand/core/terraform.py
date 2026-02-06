from pathlib import Path
from ..models import DesiredStateSpec
from ..terraform_gen import get_generator
from .paths import terraform_dir

def generate_terraform(root: Path, spec: DesiredStateSpec, project_id: str = "default", workspace_id: str = "default") -> Path:
    generator = get_generator(spec.provider)
    tf_dir = terraform_dir(root)
    generator.generate(spec, tf_dir, project_id, workspace_id)
    return tf_dir
