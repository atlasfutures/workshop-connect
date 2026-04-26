"""Tests for the ConnectorClient.

Unit tests mock the HTTP layer (httpx). Integration tests against the
dev proxy are gated by CONNECTOR_INTEGRATION_TEST=1.
"""

from __future__ import annotations

import json
import os

import httpx
import pytest

from workshop_connect.client import ConnectorClient
from workshop_connect.errors import ActionError, AuthError


class TestClientConstruction:
    def test_requires_api_key(self) -> None:
        with pytest.raises(AuthError, match="api_key"):
            ConnectorClient(
                proxy_url="https://p.com", api_key="", connected_account_id="ca"
            )

    def test_constructs_with_valid_args(self) -> None:
        c = ConnectorClient(
            proxy_url="https://p.com/",
            api_key="test_api_key",
            connected_account_id="ca_123",
        )
        assert c._proxy_url == "https://p.com"  # trailing slash stripped
        c.close()

    def test_context_manager(self) -> None:
        with ConnectorClient(
            proxy_url="https://p.com",
            api_key="test_api_key",
            connected_account_id="ca_123",
        ) as c:
            assert c._api_key == "test_api_key"


class TestExecute:
    """Mock-transport tests for action execution."""

    def _make_client(self, transport: httpx.MockTransport) -> ConnectorClient:
        c = ConnectorClient(
            proxy_url="https://proxy.test",
            api_key="test_api_key",
            connected_account_id="ca_test",
        )
        c._http = httpx.Client(transport=transport, headers=c._http.headers)
        return c

    _ACCOUNT_RESPONSE = {
        "id": "ca_test",
        "user_id": "user_123",
        "status": "ACTIVE",
        "deprecated": {"uuid": "00000000-0000-0000-0000-000000000001"},
    }

    def _account_or_execute(self, request: httpx.Request) -> httpx.Response | None:
        """Return account-resolution response, or None for execute."""
        if request.method == "GET" and "/v3/connected_accounts/" in str(request.url):
            return httpx.Response(200, json=self._ACCOUNT_RESPONSE)
        return None

    def test_successful_execute(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            acct = self._account_or_execute(request)
            if acct:
                return acct
            assert "/v3.1/tools/execute/GMAIL_GET_PROFILE" in str(request.url)
            body = json.loads(request.content)
            assert body["connectedAccountId"] == "00000000-0000-0000-0000-000000000001"
            assert body["entity_id"] == "user_123"
            assert body["arguments"] == {"userId": "me"}
            assert request.headers["authorization"] == "Bearer test_api_key"
            return httpx.Response(
                200,
                json={
                    "data": {"email": "test@example.com"},
                    "successful": True,
                    "error": None,
                    "log_id": "log_test",
                },
            )

        c = self._make_client(httpx.MockTransport(handler))
        result = c.execute("GMAIL_GET_PROFILE", {"userId": "me"})
        # execute() unwraps — returns data directly
        assert result["email"] == "test@example.com"
        c.close()

    def test_execute_raw_returns_envelope(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            acct = self._account_or_execute(request)
            if acct:
                return acct
            return httpx.Response(
                200,
                json={
                    "data": {"email": "test@example.com"},
                    "successful": True,
                    "error": None,
                    "log_id": "log_test",
                },
            )

        c = self._make_client(httpx.MockTransport(handler))
        raw = c.execute_raw("GMAIL_GET_PROFILE", {"userId": "me"})
        # execute_raw() returns the full envelope
        assert raw["successful"] is True
        assert raw["data"]["email"] == "test@example.com"
        assert raw["log_id"] == "log_test"
        c.close()

    def test_execute_raises_on_unsuccessful(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            acct = self._account_or_execute(request)
            if acct:
                return acct
            return httpx.Response(
                200,
                json={
                    "data": {},
                    "successful": False,
                    "error": {"message": "Rate limit exceeded", "status": 429},
                    "log_id": "log_fail",
                },
            )

        c = self._make_client(httpx.MockTransport(handler))
        with pytest.raises(ActionError, match="Rate limit exceeded"):
            c.execute("GMAIL_SEND_EMAIL", {})
        c.close()

    def test_error_raises_action_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            acct = self._account_or_execute(request)
            if acct:
                return acct
            return httpx.Response(403, json={"error": "forbidden"})

        c = self._make_client(httpx.MockTransport(handler))
        with pytest.raises(ActionError, match="403"):
            c.execute("BAD_ACTION", {})
        c.close()

    def test_empty_arguments_default(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            acct = self._account_or_execute(request)
            if acct:
                return acct
            body = json.loads(request.content)
            assert body["arguments"] == {}
            return httpx.Response(
                200,
                json={"data": {"status": "ok"}, "successful": True, "error": None},
            )

        c = self._make_client(httpx.MockTransport(handler))
        result = c.execute("SOME_ACTION")
        assert result["status"] == "ok"
        c.close()

    def test_entity_id_override(self) -> None:
        """Explicit entity_id param overrides auto-resolved value."""

        def handler(request: httpx.Request) -> httpx.Response:
            acct = self._account_or_execute(request)
            if acct:
                return acct
            body = json.loads(request.content)
            assert body["entity_id"] == "custom_entity"
            return httpx.Response(
                200,
                json={"data": {"ok": True}, "successful": True, "error": None},
            )

        c = self._make_client(httpx.MockTransport(handler))
        c.execute("SOME_ACTION", entity_id="custom_entity")
        c.close()

    def test_resolution_cached(self) -> None:
        """Account details are fetched only once across multiple executes."""
        resolve_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal resolve_count
            if request.method == "GET" and "/v3/connected_accounts/" in str(
                request.url
            ):
                resolve_count += 1
                return httpx.Response(200, json=self._ACCOUNT_RESPONSE)
            return httpx.Response(
                200,
                json={"data": {"ok": True}, "successful": True, "error": None},
            )

        c = self._make_client(httpx.MockTransport(handler))
        c.execute("ACTION_1")
        c.execute("ACTION_2")
        assert resolve_count == 1
        c.close()


class TestTriggers:
    """Mock-transport tests for trigger lifecycle."""

    def _make_client(self, transport: httpx.MockTransport) -> ConnectorClient:
        c = ConnectorClient(
            proxy_url="https://proxy.test",
            api_key="test_api_key",
            connected_account_id="ca_test",
        )
        c._http = httpx.Client(transport=transport, headers=c._http.headers)
        # Pre-resolve so _ensure_account_details() is a no-op in trigger tests.
        c._resolved_uuid = "ca_test"
        c._resolved_entity_id = "e_test"
        return c

    def test_trigger_create(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert "/v3/triggers/subscribe/GMAIL_NEW_MESSAGE" in str(request.url)
            return httpx.Response(200, json={"triggerId": "t_123"})

        c = self._make_client(httpx.MockTransport(handler))
        result = c.trigger_create(
            "GMAIL_NEW_MESSAGE", webhook_url="https://hook.test/cb"
        )
        assert result["triggerId"] == "t_123"
        c.close()

    def test_trigger_list(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"trigger_instances": [{"id": "t_1"}]})

        c = self._make_client(httpx.MockTransport(handler))
        result = c.trigger_list()
        assert len(result) == 1
        c.close()

    def test_trigger_disable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "PATCH"
            body = json.loads(request.content)
            assert body["enabled"] is False
            return httpx.Response(200, json={"status": "disabled"})

        c = self._make_client(httpx.MockTransport(handler))
        c.trigger_disable("t_1")
        c.close()

    def test_trigger_delete(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "DELETE"
            return httpx.Response(200, json={"deleted": True})

        c = self._make_client(httpx.MockTransport(handler))
        c.trigger_delete("t_1")
        c.close()


@pytest.mark.skipif(
    os.environ.get("CONNECTOR_INTEGRATION_TEST") != "1",
    reason="Set CONNECTOR_INTEGRATION_TEST=1 to run integration tests",
)
class TestIntegration:
    """Integration tests against the live dev proxy.

    Requires environment vars for a real connector to be set.
    """

    def test_gmail_get_profile(self) -> None:
        client = ConnectorClient.from_connector("gmail")
        result = client.execute("GMAIL_GET_PROFILE", {"userId": "me"})
        # execute() unwraps — data is returned directly
        assert "emailAddress" in result or "email" in result
        client.close()
