"""Architecture checks for the reusable ZONT protocol package."""

from __future__ import annotations

import ast
from pathlib import Path

PROTOCOL_DIR = (
    Path(__file__).parents[2] / "custom_components" / "zont_local" / "protocol"
)


def test_protocol_package_does_not_import_home_assistant() -> None:
    """Keep transport, models, parsers, and commands reusable outside HA."""
    forbidden: list[str] = []
    for path in sorted(PROTOCOL_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...]
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules = (node.module or "",)
            else:
                continue
            if any(
                module == "homeassistant" or module.startswith("homeassistant.")
                for module in modules
            ):
                forbidden.append(str(path.relative_to(PROTOCOL_DIR)))
    assert forbidden == []


def test_protocol_package_does_not_reach_into_ha_layer() -> None:
    """Prevent relative imports from escaping the protocol boundary."""
    violations: list[str] = []
    for path in sorted(PROTOCOL_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        if any(
            isinstance(node, ast.ImportFrom) and node.level > 1
            for node in ast.walk(tree)
        ):
            violations.append(str(path.relative_to(PROTOCOL_DIR)))
    assert violations == []
