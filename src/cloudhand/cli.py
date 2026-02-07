from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

import click

from .core.apply import apply_plan
from .core.config import DEFAULT_CONFIG_FILE, load_config, load_provider_config
from .core.diagram import generate_diagram, graph_to_mermaid
from .core.init import init_project
from .core.paths import cloudhand_dir, project_root
from .core.plan import generate_plan
from .core.scan import run_scan
from .core.spec import sync_spec
from .core.terraform import generate_terraform
from .models import CloudGraph
from .adapters import ProviderConfig


@click.group(invoke_without_command=True)
@click.option("--provider", "provider_opt", default=None, help="Cloud provider (hetzner/aws/digitalocean)")
@click.option("--project", "project_opt", default=None, help="Logical project name")
@click.option("--config", "config_path", default=DEFAULT_CONFIG_FILE, help="Config file path")
@click.pass_context
def cli(ctx: click.Context, provider_opt: Optional[str], project_opt: Optional[str], config_path: str) -> None:
    """Cloudhand CLI (ch) entrypoint."""

    ctx.ensure_object(dict)
    root = project_root()
    config_file = root / config_path
    file_cfg = load_config(config_file)

    provider = provider_opt or file_cfg.get("provider") or "hetzner"
    project = project_opt or file_cfg.get("project")

    ctx.obj["provider"] = provider
    ctx.obj["project"] = project
    ctx.obj["config_path"] = config_path

    # Onboarding flow: if no subcommand and no config, prompt for provider and token, then scan.
    if ctx.invoked_subcommand is None and not file_cfg:
        click.echo("Welcome to CloudHand (ch)!")
        provider_choice = provider_opt or click.prompt(
            "Select provider",
            type=click.Choice(["hetzner"], case_sensitive=False),
            default="hetzner",
        ).lower()
        project_name = project or root.name

        # Bootstrap layout
        init_project(provider_choice, project_name, root)
        click.echo(f"Initialized cloudhand project in {root}")

        token = click.prompt(
            f"Enter {provider_choice} API token (leave blank to use HCLOUD_TOKEN env var)",
            default="",
            hide_input=True,
            show_default=False,
        )

        cfg = ProviderConfig()
        if token:
            cfg["token"] = token

            # Persist token into cloudhand/secrets.json (gitignored)
            secrets_path = cloudhand_dir(root) / "secrets.json"
            secrets: dict = {}
            if secrets_path.exists():
                try:
                    secrets = json.loads(secrets_path.read_text(encoding="utf-8"))
                except Exception:
                    secrets = {}
            providers = secrets.setdefault("providers", {})
            providers[provider_choice] = {"token": token}
            secrets_path.write_text(json.dumps(secrets, indent=2), encoding="utf-8")

        run_scan(root, provider_choice, cfg)
        click.echo("Initial scan complete. You can now run 'ch diagram', 'ch render', or 'ch sync-spec'.")
        ctx.exit(0)


@cli.command()
@click.option("--provider", default="hetzner", help="Cloud provider to use for this repo")
@click.option("--project", required=True, help="Project name")
def init(provider: str, project: str) -> None:
    """Bootstrap repo layout (terraform, diagrams, config)."""
    init_project(provider, project)
    click.echo(f"Initialized cloudhand project in {project_root()}")


@cli.command()
@click.option("--provider", default=None, help="Override provider for this scan")
@click.pass_context
def scan(ctx: click.Context, provider: Optional[str]) -> None:
    """Discover live resources from the selected cloud provider."""
    provider = provider or ctx.obj.get("provider") or "hetzner"
    cfg = load_provider_config(provider)
    graph = run_scan(project_root(), provider, cfg)
    click.echo(f"Scanned {len(graph.nodes)} nodes and {len(graph.edges)} edges.")


@cli.command()
@click.option("--output", "output_path", default=None, help="Output .mmd path")
@click.pass_context
def diagram(ctx: click.Context, output_path: Optional[str]) -> None:
    """Convert scan.json to Mermaid diagram."""
    try:
        out = generate_diagram(project_root(), use_llm=True, output_path=Path(output_path) if output_path else None)
        click.echo(f"Wrote Mermaid diagram to {out}")
    except Exception as e:
        raise click.ClickException(str(e))


@cli.command()
@click.option("--format", "fmt", default="svg", type=click.Choice(["svg", "png", "pdf"]))
@click.option("--input", "input_path", default=None, help="Input .mmd path")
@click.option("--output", "output_path", default=None, help="Output image path")
@click.option("--open/--no-open", "open_flag", default=False, help="Open the rendered file")
def render(fmt: str, input_path: Optional[str], output_path: Optional[str], open_flag: bool) -> None:
    """Render Mermaid .mmd to image via mmdc."""

    mmdc = shutil.which("mmdc")
    if mmdc is None:
        raise click.ClickException(
            "Mermaid CLI 'mmdc' not found in PATH. Install @mermaid-js/mermaid-cli first."
        )

    diag_dir = cloudhand_dir() / "diagrams" # Re-using logic from paths but accessed via cloudhand_dir
    if input_path is None:
        input_path = str(diag_dir / "current.mmd")
    if output_path is None:
        output_path = str(diag_dir / f"current.{fmt}")

    cmd = [
        mmdc,
        "-i",
        input_path,
        "-o",
        output_path,
        "-e",
        fmt,
    ]

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        # If rendering fails, attempt to install Playwright browsers automatically,
        # since mermaid-cli depends on a headless browser for SVG/PNG output.
        playwright_bin = shutil.which("playwright")
        if not playwright_bin:
            raise click.ClickException(
                "mmdc failed and Playwright is not available. "
                "Ensure mermaid-cli and Playwright are installed in your environment."
            )
        try:
            subprocess.run([playwright_bin, "install", "chromium"], check=True)
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as exc2:
            raise click.ClickException(f"mmdc failed even after installing Playwright browsers: {exc2}") from exc2

    click.echo(f"Rendered diagram to {output_path}")

    if open_flag:
        if os.name == "posix":
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.run([opener, output_path], check=False)


@cli.command("sync-spec")
@click.pass_context
def sync_spec_cmd(ctx: click.Context) -> None:
    """Generate spec.json and Terraform from the latest scan.json."""
    provider = ctx.obj.get("provider") or "hetzner"
    try:
        spec = sync_spec(project_root(), provider)
        tf_dir = generate_terraform(project_root(), spec)
        click.echo(f"Wrote spec to {cloudhand_dir() / 'spec.json'} and Terraform configuration under {tf_dir}")
    except Exception as e:
        raise click.ClickException(str(e))


@cli.command()
@click.argument("description")
@click.pass_context
def plan(ctx: click.Context, description: str) -> None:
    """Generate a change plan using an LLM."""
    try:
        plan_body, plan_path = generate_plan(project_root(), description)
        click.echo(f"Generated plan: {plan_path}")
        if plan_body.get("error"):
            click.echo(f"Warning: {plan_body['error']}", err=True)
    except Exception as e:
        raise click.ClickException(str(e))


@cli.command()
@click.argument("plan_file")
@click.option("--auto-approve", is_flag=True, help="Skip interactive approval")
@click.pass_context
def apply(ctx: click.Context, plan_file: str, auto_approve: bool) -> None:
    """Apply a plan."""
    try:
        project_id = ((ctx.obj or {}).get("project")) or "default"
        ret = apply_plan(
            project_root(),
            Path(plan_file),
            auto_approve,
            project_id=project_id,
            workspace_id=project_id,
        )
        if ret != 0:
            sys.exit(ret)
    except Exception as e:
        raise click.ClickException(str(e))

main = cli


if __name__ == "__main__":
    # Allow `python -m cloudhand.cli ...` (used by the control-plane API subprocess calls).
    main()
