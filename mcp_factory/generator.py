"""Generator — scaffolds MCP server stubs from Manifest objects."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from mcp_factory.manifest import Manifest

# Package-relative resolution (importlib.resources) so this works identically
# from a repo checkout AND a wheel/sdist install — templates ship as package
# data under mcp_factory/templates/ (see pyproject.toml package-data), not at
# the repo root, so a pip-installed wheel (which only ships the mcp_factory/
# tree) still finds them.
_TEMPLATES_DIR = Path(str(resources.files("mcp_factory") / "templates"))

# Manifest arg type -> Python type hint, used by the fastmcp template so
# generated tool signatures carry real types instead of raw JSON-schema dicts.
_PY_TYPE_MAP = {
    "string": "str",
    "number": "float",
    "boolean": "bool",
    "object": "dict",
    "array": "list",
}


def _pytype(arg_type: str) -> str:
    return _PY_TYPE_MAP.get(arg_type, "str")


# --- Security: serialize untrusted free-text into code, don't hand-escape ---
#
# Autoescape posture: these templates emit source CODE (.py/.js), not HTML, so
# HTML autoescaping them would corrupt the output. The real, load-bearing
# control against codegen injection is therefore NOT autoescape — it is the
# two layers below: (1) parse-time charset validation of every identifier slot
# in manifest.py (manifest.name / tool.name / arg.name / env_required, which
# render as bare identifiers or unescaped list entries and so must fail closed
# if not a strict identifier), and (2) serializer filters here (py_str / js_str
# / docstring_safe / js_comment) for every free-text slot. autoescape is scoped
# with ``select_autoescape(["html", "htm", "xml"])`` — the Jinja-recommended
# default — so it is a no-op for the current code templates but auto-escapes any
# HTML-family template that might later be added (defense in depth), rather than
# the previous ``select_autoescape([])`` which disabled escaping unconditionally.
#
# Free-text manifest fields (tool.description, arg.description, manifest.description,
# runtime.command) are NOT charset-validated — they may contain quotes,
# backslashes, and the full Unicode line-terminator class. Hand-rolled
# ``replace('"', '\\"')`` is fragile: it misses ``\r`` / U+2028 / U+2029 / NEL
# and closing-delimiter tokens, which historically let a crafted value break
# out of a comment/string and inject executable code on the buyer's machine.
#
# The durable control is to let the target language's own serializer produce
# the literal so EVERY dangerous character is handled at once:
#   * ``py_str``  -> repr(): a complete, correctly-quoted Python string literal.
#   * ``js_str``  -> json.dumps(): a complete, valid JS/JSON string literal
#                    (ensure_ascii escapes U+2028/U+2029 too).
# For slots that must stay inside a comment/docstring (documentation lines that
# can't be a string literal) we neutralize the only characters that can escape
# that context:
#   * ``docstring_safe`` collapses any run of 3+ double-quotes so free text can
#     never close a triple-quoted (``r"""``) docstring. Newlines are legal
#     inside a triple-quoted string, so they need no handling there.
#   * ``js_comment`` replaces the FULL Unicode line-terminator class (not just
#     ``\n``) with ``\n// `` so a terminator can't end the ``//`` comment and
#     turn the remainder into live JS.

# JS line terminators per the ECMAScript spec: LF, CR, CRLF, LS (U+2028),
# PS (U+2029). Also fold the extra Unicode terminators (NEL U+0085, VT, FF)
# that some parsers/tools treat as line breaks — belt and suspenders.
_LINE_TERMINATORS = re.compile(r"\r\n|[\n\r  ]")
# Any run of 3+ double-quotes could close a `"""` docstring; collapse to 2.
_TRIPLE_DQUOTE_RUN = re.compile(r'"{3,}')


def _py_str(value: object) -> str:
    """Render a value as a complete, safe Python string literal via repr()."""
    return repr(str(value))


def _js_str(value: object) -> str:
    """Render a value as a complete, safe JS/JSON string literal via json.dumps()."""
    return json.dumps(str(value))


def _docstring_safe(value: object) -> str:
    """Neutralize a value for placement inside a triple-quoted (r\"\"\") docstring.

    The only way free text can escape a ``r\"\"\"...\"\"\"`` docstring is a literal
    run of three-or-more double-quotes closing the delimiter; collapse any such
    run to two so no ``\"\"\"`` remains. Newlines are legal inside the docstring
    and are preserved.
    """
    return _TRIPLE_DQUOTE_RUN.sub('""', str(value))


def _js_comment(value: object) -> str:
    """Neutralize a value for placement inside a JS ``//`` line comment.

    Replaces the full Unicode line-terminator class with ``\\n// `` so a
    terminator ends the comment cleanly and every continuation line stays
    commented — a bare terminator can otherwise drop the remainder into live JS.
    """
    return _LINE_TERMINATORS.sub("\n// ", str(value))


def _resolve_output_path(manifest: Manifest, output_dir: Path, default_name: str) -> Path:
    """Resolve where the scaffold is written, confining it under ``output_dir``.

    Security: ``manifest.runtime.output`` comes from an untrusted manifest. Used
    verbatim it allows an absolute path or ``..`` traversal to write generated
    (attacker-controlled) source anywhere on the buyer's filesystem. This
    resolves the path against ``output_dir`` and rejects anything that escapes
    it (absolute paths, drive-qualified paths, or ``..`` traversal) via a
    realpath containment check — fail closed before any file is written.
    """
    output_dir_resolved = output_dir.resolve()

    if manifest.runtime.output_path is None:
        return output_dir_resolved / default_name

    raw = manifest.runtime.output
    candidate = Path(raw)
    if candidate.is_absolute() or candidate.drive or candidate.root:
        raise ValueError(
            f"runtime.output {raw!r} must be a relative path inside the output "
            "directory; absolute paths are rejected for safety."
        )

    resolved = (output_dir_resolved / candidate).resolve()
    if resolved != output_dir_resolved and output_dir_resolved not in resolved.parents:
        raise ValueError(
            f"runtime.output {raw!r} escapes the output directory "
            f"{str(output_dir_resolved)!r}; path traversal ('..') is rejected."
        )
    return resolved


def _get_jinja_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "htm", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters["pytype"] = _pytype
    env.filters["py_str"] = _py_str
    env.filters["js_str"] = _js_str
    env.filters["docstring_safe"] = _docstring_safe
    env.filters["js_comment"] = _js_comment
    return env


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
    template_name = "python_fastmcp.j2" if manifest.runtime.style == "fastmcp" else "python_server.py.j2"
    template = env.get_template(template_name)

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    content = template.render(manifest=manifest, generated_at=generated_at)

    # Use manifest.runtime.output if specified, else default to output_dir/<name>_server.py.
    # _resolve_output_path confines the target under output_dir (see its docstring).
    out_path = _resolve_output_path(
        manifest, output_dir, f"{manifest.name.replace('-', '_')}_server.py"
    )

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

    out_path = _resolve_output_path(
        manifest, output_dir, f"{manifest.name.replace('-', '_')}_server.js"
    )

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
