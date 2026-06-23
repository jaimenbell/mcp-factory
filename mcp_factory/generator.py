"""Generator — scaffolds MCP server stubs from Manifest objects."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from mcp_factory.manifest import Manifest

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def _get_jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape([]),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def generate_server(
    manifest: Manifest,
    output_dir: Path | str,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> Path | None:
    """Scaffold a new MCP server stub from a manifest.

    Returns the output path if a file was written, None if skipped (existing script) or dry_run.

    Skips generation if manifest.runtime.has_existing_script — the factory
    references the existing file rather than overwriting it.
    """
    output_dir = Path(output_dir)

    if manifest.runtime.has_existing_script:
        return None

    if manifest.runtime.type == "python":
        return _generate_python_server(manifest, output_dir, force=force, dry_run=dry_run)
    elif manifest.runtime.type == "node":
        return _generate_node_server(manifest, output_dir, force=force, dry_run=dry_run)
    else:
        raise NotImplementedError(f"No scaffold template for runtime type '{manifest.runtime.type}'")


def _generate_python_server(
    manifest: Manifest,
    output_dir: Path,
    *,
    force: bool,
    dry_run: bool,
) -> Path | None:
    env = _get_jinja_env()
    template = env.get_template("python_server.py.j2")

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    content = template.render(manifest=manifest, generated_at=generated_at)

    # Use manifest.runtime.output if specified, else default to output_dir/<name>_server.py
    if manifest.runtime.output_path:
        out_path = manifest.runtime.output_path
    else:
        out_path = output_dir / f"{manifest.name.replace('-', '_')}_server.py"

    if dry_run:
        print(f"[dry-run] Would write {out_path} ({len(content)} chars)")
        print(content)
        return None

    if out_path.exists() and not force:
        raise FileExistsError(
            f"Output file already exists: {out_path}\n"
            "Use --force to overwrite, or set runtime.script in your manifest to reference it."
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    return out_path


def _generate_node_server(
    manifest: Manifest,
    output_dir: Path,
    *,
    force: bool,
    dry_run: bool,
) -> Path | None:
    env = _get_jinja_env()
    template = env.get_template("node_server.js.j2")

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    content = template.render(manifest=manifest, generated_at=generated_at)

    if manifest.runtime.output_path:
        out_path = manifest.runtime.output_path
    else:
        out_path = output_dir / f"{manifest.name.replace('-', '_')}_server.js"

    if dry_run:
        print(f"[dry-run] Would write {out_path} ({len(content)} chars)")
        print(content)
        return None

    if out_path.exists() and not force:
        raise FileExistsError(
            f"Output file already exists: {out_path}\n"
            "Use --force to overwrite."
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    return out_path


def scaffold_summary(manifest: Manifest, output_dir: Path) -> dict:
    """Return a summary dict of what generate_server() would produce (no file I/O)."""
    if manifest.runtime.has_existing_script:
        return {
            "action": "reference_existing",
            "script": manifest.runtime.script,
            "tools": manifest.tool_names,
        }
    if manifest.runtime.output_path:
        out_path = manifest.runtime.output_path
    else:
        ext = ".js" if manifest.runtime.type == "node" else ".py"
        out_path = output_dir / f"{manifest.name.replace('-', '_')}_server{ext}"
    return {
        "action": "scaffold_new",
        "output_path": str(out_path),
        "tools": manifest.tool_names,
    }
