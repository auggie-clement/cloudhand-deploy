from pathlib import Path
from typing import Optional
from .paths import project_root, terraform_dir, diagrams_dir, cloudhand_dir

def init_project(provider: str, project: str, root: Optional[Path] = None) -> None:
    root = project_root(root)
    tf_dir = terraform_dir(root)
    diag_dir = diagrams_dir(root)
    ch_dir = cloudhand_dir(root)

    for d in [tf_dir, diag_dir / "history", ch_dir]:
        d.mkdir(parents=True, exist_ok=True)

    gitignore_path = root / ".gitignore"
    if not gitignore_path.exists():
        gitignore_path.write_text(
            "cloudhand/terraform/.terraform/\n"
            "cloudhand/terraform/terraform.tfstate\n"
            "cloudhand/terraform/terraform.tfstate.*\n"
            "cloudhand/terraform/*.tfplan\n"
            "cloudhand/*.json\n"
            "cloudhand/*.diff\n",
            encoding="utf-8",
        )

    ch_yaml = root / "ch.yaml"
    if not ch_yaml.exists():
        ch_yaml.write_text(
            f"provider: {provider}\nproject: {project}\n",
            encoding="utf-8",
        )
