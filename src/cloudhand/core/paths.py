from pathlib import Path
from typing import Optional

def project_root(base: Optional[Path] = None) -> Path:
    return base or Path.cwd()

def cloudhand_dir(root: Optional[Path] = None) -> Path:
    return project_root(root) / "cloudhand"

def diagrams_dir(root: Optional[Path] = None) -> Path:
    return cloudhand_dir(root) / "diagrams"

def terraform_dir(root: Optional[Path] = None) -> Path:
    return cloudhand_dir(root) / "terraform"
