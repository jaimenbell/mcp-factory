"""Templates must live inside the mcp_factory package, not the repo root.

A `pip install`-ed wheel only ships the `mcp_factory` package tree (per
`[tool.setuptools.packages.find] include = ["mcp_factory*"]`). Before this
fix, templates/*.j2 lived at the repo root — outside that tree — so a wheel
install's scaffold mode (`--manifest`) raised jinja2.TemplateNotFound the
moment generate_server() was called. See mcp_factory/generator.py.

These tests resolve the templates dir via `importlib.resources` against the
INSTALLED `mcp_factory` package (not a repo-root-relative Path), so they only
pass when the templates are actually packaged data.
"""
from __future__ import annotations

import sys
from importlib import resources
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp_factory import generator
from mcp_factory.manifest import load_manifest

_EXAMPLES = Path(__file__).parent.parent / "examples"

_TEMPLATE_NAMES = ["python_server.py.j2", "python_fastmcp.j2", "node_server.js.j2"]


class TestTemplatesArePackaged:
    @pytest.mark.parametrize("name", _TEMPLATE_NAMES)
    def test_template_resolves_via_importlib_resources(self, name):
        """Each template must be reachable through the mcp_factory package,
        not a repo-root-relative path — this is what a wheel install has."""
        packaged = resources.files("mcp_factory") / "templates" / name
        assert packaged.is_file(), f"{name} not found inside the mcp_factory package"

    def test_generator_templates_dir_is_inside_the_package(self):
        """generator._TEMPLATES_DIR must resolve under mcp_factory/, not the
        repo root — otherwise a wheel install (which only ships mcp_factory/)
        has no templates dir at all."""
        pkg_dir = Path(generator.__file__).resolve().parent
        templates_dir = Path(str(generator._TEMPLATES_DIR)).resolve()
        assert templates_dir.is_relative_to(pkg_dir), (
            f"_TEMPLATES_DIR ({templates_dir}) is not inside the mcp_factory "
            f"package dir ({pkg_dir}) — a wheel install would not ship it"
        )

    @pytest.mark.parametrize("name", _TEMPLATE_NAMES)
    def test_jinja_env_loads_each_template(self, name):
        """The generator's own jinja Environment must be able to load every
        template by name — the actual code path a wheel-installed scaffold
        run exercises."""
        env = generator._get_jinja_env()
        template = env.get_template(name)
        assert template is not None

    def test_generate_server_end_to_end_python(self, tmp_path):
        manifest = load_manifest(_EXAMPLES / "fastmcp_example.yaml")
        out_path = generator.generate_server(manifest, tmp_path, force=True)
        assert out_path is not None
        assert out_path.exists()
        assert out_path.read_text(encoding="utf-8").strip()
