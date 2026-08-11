"""Tests for ZONT Local translation resources."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from string import Formatter
from typing import Any

from homeassistant.util.yaml import load_yaml_dict

INTEGRATION_PATH = Path(__file__).parents[1] / "custom_components" / "zont_local"
TRANSLATIONS_PATH = INTEGRATION_PATH / "translations"


def _load_translation(language: str) -> dict[str, Any]:
    """Load one integration translation file."""
    return json.loads((TRANSLATIONS_PATH / f"{language}.json").read_text())


def _string_leaves(
    value: dict[str, Any], path: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], str]]:
    """Yield paths and values for all translated strings."""
    for key, child in value.items():
        child_path = (*path, key)
        if isinstance(child, dict):
            yield from _string_leaves(child, child_path)
        else:
            assert isinstance(child, str), f"{'.'.join(child_path)} is not a string"
            yield child_path, child


def _placeholders(value: str) -> set[str]:
    """Return format placeholders used by one translation string."""
    return {
        field_name
        for _, field_name, _, _ in Formatter().parse(value)
        if field_name is not None
    }


def test_translation_files_have_matching_complete_structure() -> None:
    """English fallback and Russian localization must stay in sync."""
    translations = {
        language: dict(_string_leaves(_load_translation(language)))
        for language in ("en", "ru")
    }

    assert translations["en"].keys() == translations["ru"].keys()
    for path, russian_value in translations["ru"].items():
        english_value = translations["en"][path]
        assert russian_value.strip(), f"Empty Russian translation: {'.'.join(path)}"
        assert english_value.strip(), f"Empty English translation: {'.'.join(path)}"
        assert "[%key:" not in russian_value
        assert "[%key:" not in english_value
        assert _placeholders(english_value) == _placeholders(russian_value), (
            f"Placeholder mismatch: {'.'.join(path)}"
        )


def test_custom_integration_uses_translation_files_only() -> None:
    """Custom integrations must not rely on Core translation build inputs."""
    assert not (INTEGRATION_PATH / "strings.json").exists()
    assert "title" not in _load_translation("en")
    assert "title" not in _load_translation("ru")


def test_service_actions_are_localized() -> None:
    """Service action labels belong in both translation files, not YAML."""
    services = load_yaml_dict(str(INTEGRATION_PATH / "services.yaml"))

    for language in ("en", "ru"):
        translated_services = _load_translation(language)["services"]
        assert translated_services.keys() == services.keys()
        for service_name, service_schema in services.items():
            assert "name" not in service_schema
            assert "description" not in service_schema
            translation = translated_services[service_name]
            assert translation["name"]
            assert translation["description"]
            assert (
                translation.get("fields", {}).keys()
                == service_schema.get("fields", {}).keys()
            )
            for field_name, field_schema in service_schema.get("fields", {}).items():
                assert "name" not in field_schema
                assert "description" not in field_schema
                assert translation["fields"][field_name]["name"]
                assert translation["fields"][field_name]["description"]
