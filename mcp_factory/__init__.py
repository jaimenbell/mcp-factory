"""mcp-factory — manifest-driven MCP server scaffolder."""
from mcp_factory.manifest import Manifest, load_manifest
from mcp_factory.generator import generate_server
from mcp_factory.config import build_claude_entry

__all__ = ["Manifest", "load_manifest", "generate_server", "build_claude_entry"]
