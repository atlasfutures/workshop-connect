"""Tests for the CLI and catalog."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from workshop_connect.catalog import (
    get_action,
    get_toolkit,
    list_toolkits,
    load_catalog,
)
from workshop_connect.cli import _coerce_value, _parse_extra_args, main
from workshop_connect.errors import CatalogError


class TestCatalog:
    """Catalog loading and lookup tests."""

    def test_catalog_loads(self) -> None:
        catalog = load_catalog()
        assert catalog["version"] == "1"
        assert "toolkits" in catalog
        assert len(catalog["toolkits"]) > 0

    def test_list_toolkits(self) -> None:
        toolkits = list_toolkits()
        assert len(toolkits) > 0
        slugs = [t["slug"] for t in toolkits]
        assert "gmail" in slugs
        assert "slack" in slugs

    def test_get_toolkit(self) -> None:
        tk = get_toolkit("gmail")
        assert tk["slug"] == "gmail"
        assert len(tk["actions"]) > 0

    def test_get_toolkit_with_hyphens(self) -> None:
        tk = get_toolkit("api-bible")
        assert tk["slug"] == "api-bible"

    def test_get_toolkit_missing_raises(self) -> None:
        with pytest.raises(CatalogError, match="not found"):
            get_toolkit("nonexistent_toolkit")

    def test_get_action(self) -> None:
        action = get_action("gmail", "GMAIL_GET_PROFILE")
        assert action["name"] == "GMAIL_GET_PROFILE"
        assert "description" in action

    def test_get_action_missing_raises(self) -> None:
        with pytest.raises(CatalogError, match="not found"):
            get_action("gmail", "NONEXISTENT_ACTION")


class TestArgParsing:
    """Extra arg parsing and coercion tests."""

    def test_key_value(self) -> None:
        result = _parse_extra_args(["--userId=me"])
        assert result == {"userId": "me"}

    def test_key_space_value(self) -> None:
        result = _parse_extra_args(["--userId", "me"])
        assert result == {"userId": "me"}

    def test_boolean_flag(self) -> None:
        result = _parse_extra_args(["--includeSpam"])
        assert result == {"includeSpam": True}

    def test_integer_coercion(self) -> None:
        result = _parse_extra_args(["--maxResults=10"])
        assert result == {"maxResults": 10}

    def test_float_coercion(self) -> None:
        result = _parse_extra_args(["--threshold=0.5"])
        assert result == {"threshold": 0.5}

    def test_bool_coercion(self) -> None:
        assert _coerce_value("true") is True
        assert _coerce_value("false") is False

    def test_leading_zero_preserved(self) -> None:
        """Leading-zero strings (IDs, phone numbers) stay as strings."""
        assert _coerce_value("012345") == "012345"
        assert _coerce_value("007") == "007"
        # Plain zero still coerces to int
        assert _coerce_value("0") == 0
        # Decimal leading zero (e.g. 0.5) still coerces to float
        assert _coerce_value("0.5") == 0.5

    def test_json_list_coercion(self) -> None:
        result = _parse_extra_args(['--ids=["a","b"]'])
        assert result == {"ids": ["a", "b"]}

    def test_mixed_args(self) -> None:
        result = _parse_extra_args(["--userId=me", "--maxResults=5", "--includeSpam"])
        assert result == {"userId": "me", "maxResults": 5, "includeSpam": True}


class TestCLI:
    """Click CLI integration tests."""

    def test_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "Workshop connector CLI" in result.output
        assert "gmail" in result.output

    def test_list(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["list"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        slugs = [t["slug"] for t in data]
        assert "gmail" in slugs

    def test_list_pretty(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--pretty", "list"])
        assert result.exit_code == 0
        assert "  " in result.output
        data = json.loads(result.output)
        assert len(data) > 0

    def test_toolkit_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["gmail", "--help"])
        assert result.exit_code == 0
        assert "GMAIL_GET_PROFILE" in result.output

    def test_toolkit_no_subcommand_lists_actions(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["gmail"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["toolkit"] == "gmail"
        action_names = [a["name"] for a in data["actions"]]
        assert "GMAIL_GET_PROFILE" in action_names

    def test_triggers_available(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["gmail", "triggers", "available"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        trigger_names = [t["name"] for t in data]
        assert "GMAIL_NEW_GMAIL_MESSAGE" in trigger_names

    def test_action_requires_connector(self) -> None:
        """Executing an action without connector env vars should exit 3."""
        runner = CliRunner()
        result = runner.invoke(main, ["gmail", "GMAIL_GET_PROFILE", "--userId=me"])
        assert result.exit_code == 3
        assert (
            "connector" in result.output.lower()
            or "connector" in (result.output + str(result.exception or "")).lower()
        )
