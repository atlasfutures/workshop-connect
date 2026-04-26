"""Tests for connector resolution logic (no network)."""

from __future__ import annotations

import os

import pytest

from workshop_connect.connector import Connector
from workshop_connect.errors import AuthError, ConnectorNotFoundError


class TestResolvePrefix:
    """Explicit prefix resolution."""

    def test_resolves_valid_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ABCDEF_COMPOSIO_PROXY_URL", "https://proxy.example.com")
        monkeypatch.setenv("ABCDEF_COMPOSIO_CONNECTED_ACCOUNT_ID", "ca_123")
        monkeypatch.setenv("ABCDEF_WORKSHOP_API_KEY", "test_api_key")
        c = Connector.from_env(prefix="ABCDEF")
        assert c.prefix == "ABCDEF"
        assert c.proxy_url == "https://proxy.example.com"
        assert c.connected_account_id == "ca_123"
        assert c.api_key == "test_api_key"

    def test_strips_trailing_slash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XYZXYZ_COMPOSIO_PROXY_URL", "https://proxy.example.com/")
        monkeypatch.setenv("XYZXYZ_COMPOSIO_CONNECTED_ACCOUNT_ID", "ca_1")
        monkeypatch.setenv("XYZXYZ_WORKSHOP_API_KEY", "test_k")
        c = Connector.from_env(prefix="xyzxyz")  # lowercase input
        assert c.prefix == "XYZXYZ"
        assert c.proxy_url == "https://proxy.example.com"

    def test_missing_proxy_url_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NOPFX_COMPOSIO_PROXY_URL", raising=False)
        with pytest.raises(ConnectorNotFoundError, match="NOPFX"):
            Connector.from_env(prefix="NOPFX")

    def test_missing_api_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BADPFX_COMPOSIO_PROXY_URL", "https://p.com")
        monkeypatch.setenv("BADPFX_COMPOSIO_CONNECTED_ACCOUNT_ID", "ca_1")
        monkeypatch.delenv("BADPFX_WORKSHOP_API_KEY", raising=False)
        with pytest.raises(AuthError, match="WORKSHOP_API_KEY"):
            Connector.from_env(prefix="BADPFX")

    def test_missing_account_id_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BADPFX_COMPOSIO_PROXY_URL", "https://p.com")
        monkeypatch.delenv("BADPFX_COMPOSIO_CONNECTED_ACCOUNT_ID", raising=False)
        monkeypatch.setenv("BADPFX_WORKSHOP_API_KEY", "test_k")
        with pytest.raises(AuthError, match="CONNECTED_ACCOUNT_ID"):
            Connector.from_env(prefix="BADPFX")


class TestAutoDetect:
    """Environment scanning without explicit prefix."""

    def _clear_composio_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Remove any pre-existing *_COMPOSIO_PROXY_URL vars."""
        for key in list(os.environ):
            if key.endswith("_COMPOSIO_PROXY_URL"):
                monkeypatch.delenv(key, raising=False)

    def test_single_connector_auto_detected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._clear_composio_env(monkeypatch)
        monkeypatch.setenv("MYGMAL_COMPOSIO_PROXY_URL", "https://p.com")
        monkeypatch.setenv("MYGMAL_COMPOSIO_CONNECTED_ACCOUNT_ID", "ca_1")
        monkeypatch.setenv("MYGMAL_WORKSHOP_API_KEY", "test_k")
        c = Connector.from_env()
        assert c.prefix == "MYGMAL"

    def test_toolkit_hint_narrows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._clear_composio_env(monkeypatch)
        for pfx, key in [("GMAILX", "test_g"), ("SLACKX", "test_s")]:
            monkeypatch.setenv(f"{pfx}_COMPOSIO_PROXY_URL", "https://p.com")
            monkeypatch.setenv(f"{pfx}_COMPOSIO_CONNECTED_ACCOUNT_ID", "ca_1")
            monkeypatch.setenv(f"{pfx}_WORKSHOP_API_KEY", key)
        c = Connector.from_env(toolkit="gmail")
        assert c.prefix == "GMAILX"

    def test_no_connectors_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._clear_composio_env(monkeypatch)
        with pytest.raises(ConnectorNotFoundError, match="No connector"):
            Connector.from_env()

    def test_ambiguous_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._clear_composio_env(monkeypatch)
        for pfx in ("AAAAAA", "BBBBBB"):
            monkeypatch.setenv(f"{pfx}_COMPOSIO_PROXY_URL", "https://p.com")
            monkeypatch.setenv(f"{pfx}_COMPOSIO_CONNECTED_ACCOUNT_ID", "ca_1")
            monkeypatch.setenv(f"{pfx}_WORKSHOP_API_KEY", "test_k")
        with pytest.raises(ConnectorNotFoundError, match="Multiple"):
            Connector.from_env()

    def test_app_name_resolves_multi_connector(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """APP_NAME env var reliably maps prefix→toolkit."""
        self._clear_composio_env(monkeypatch)
        for pfx, app in [
            ("DAVIDG", "gmail"),
            ("DAVID1", "googlecalendar"),
            ("SLACKA", "slack"),
        ]:
            monkeypatch.setenv(f"{pfx}_COMPOSIO_PROXY_URL", "https://p.com")
            monkeypatch.setenv(f"{pfx}_COMPOSIO_CONNECTED_ACCOUNT_ID", "ca_1")
            monkeypatch.setenv(f"{pfx}_WORKSHOP_API_KEY", "test_k")
            monkeypatch.setenv(f"{pfx}_COMPOSIO_APP_NAME", app)
        c = Connector.from_env(toolkit="googlecalendar")
        assert c.prefix == "DAVID1"

    def test_ambiguous_error_shows_app_names(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Error message includes prefix→app mapping."""
        self._clear_composio_env(monkeypatch)
        for pfx, app in [("AAAAAA", "gmail"), ("BBBBBB", "slack")]:
            monkeypatch.setenv(f"{pfx}_COMPOSIO_PROXY_URL", "https://p.com")
            monkeypatch.setenv(f"{pfx}_COMPOSIO_CONNECTED_ACCOUNT_ID", "ca_1")
            monkeypatch.setenv(f"{pfx}_WORKSHOP_API_KEY", "test_k")
            monkeypatch.setenv(f"{pfx}_COMPOSIO_APP_NAME", app)
        with pytest.raises(ConnectorNotFoundError, match=r"AAAAAA \(gmail\)"):
            Connector.from_env()
