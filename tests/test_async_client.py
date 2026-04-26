"""Tests for the AsyncConnectorClient."""

from __future__ import annotations

import json

import httpx
import pytest

from workshop_connect.async_client import AsyncConnectorClient
from workshop_connect.errors import ActionError, AuthError


class TestAsyncClientConstruction:
    def test_requires_api_key(self) -> None:
        with pytest.raises(AuthError, match="api_key"):
            AsyncConnectorClient(
                proxy_url="https://p.com", api_key="", connected_account_id="ca"
            )

    def test_constructs_with_valid_args(self) -> None:
        c = AsyncConnectorClient(
            proxy_url="https://p.com/",
            api_key="test_api_key",
            connected_account_id="ca_123",
        )
        assert c._proxy_url == "https://p.com"
        # Don't await close in sync test — just verify construction

    async def test_async_context_manager(self) -> None:
        async with AsyncConnectorClient(
            proxy_url="https://p.com",
            api_key="test_api_key",
            connected_account_id="ca_123",
        ) as c:
            assert c._api_key == "test_api_key"


class TestAsyncExecute:
    """Mock-transport tests for async action execution."""

    _ACCOUNT_RESPONSE = {
        "id": "ca_test",
        "user_id": "user_123",
        "status": "ACTIVE",
        "deprecated": {"uuid": "00000000-0000-0000-0000-000000000001"},
    }

    def _make_client(self, handler) -> AsyncConnectorClient:
        c = AsyncConnectorClient(
            proxy_url="https://proxy.test",
            api_key="test_api_key",
            connected_account_id="ca_test",
        )
        c._resolved_uuid = "00000000-0000-0000-0000-000000000001"
        c._resolved_entity_id = "user_123"
        c._http = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers=c._http.headers)
        return c

    async def test_successful_execute(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert "/v3.1/tools/execute/GMAIL_GET_PROFILE" in str(request.url)
            body = json.loads(request.content)
            assert body["connectedAccountId"] == "00000000-0000-0000-0000-000000000001"
            assert body["arguments"] == {"userId": "me"}
            return httpx.Response(200, json={"data": {"email": "test@example.com"}})

        c = self._make_client(handler)
        result = await c.execute("GMAIL_GET_PROFILE", {"userId": "me"})
        assert result["data"]["email"] == "test@example.com"
        await c.close()

    async def test_error_raises_action_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"error": "forbidden"})

        c = self._make_client(handler)
        with pytest.raises(ActionError, match="403"):
            await c.execute("BAD_ACTION", {})
        await c.close()

    async def test_empty_arguments_default(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert body["arguments"] == {}
            return httpx.Response(200, json={"ok": True})

        c = self._make_client(handler)
        await c.execute("SOME_ACTION")
        await c.close()

    async def test_entity_id_override(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert body["entity_id"] == "custom_entity"
            return httpx.Response(200, json={"ok": True})

        c = self._make_client(handler)
        await c.execute("SOME_ACTION", {}, entity_id="custom_entity")
        await c.close()


class TestAsyncTriggers:
    """Mock-transport tests for async trigger management."""

    def _make_client(self, handler) -> AsyncConnectorClient:
        c = AsyncConnectorClient(
            proxy_url="https://proxy.test",
            api_key="test_api_key",
            connected_account_id="ca_test",
        )
        c._resolved_uuid = "uuid-001"
        c._resolved_entity_id = "entity-001"
        c._http = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers=c._http.headers)
        return c

    async def test_trigger_create(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert "/v3/triggers/subscribe/GMAIL_NEW_MESSAGE" in str(request.url)
            body = json.loads(request.content)
            assert body["webhookURL"] == "https://hook.test/123"
            return httpx.Response(200, json={"triggerId": "t_001"})

        c = self._make_client(handler)
        result = await c.trigger_create("GMAIL_NEW_MESSAGE", webhook_url="https://hook.test/123")
        assert result["triggerId"] == "t_001"
        await c.close()

    async def test_trigger_list(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert "/v3/triggers/active_triggers" in str(request.url)
            return httpx.Response(200, json={"triggers": [{"id": "t_001"}]})

        c = self._make_client(handler)
        result = await c.trigger_list()
        assert len(result) == 1
        await c.close()

    async def test_trigger_delete(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert "/v3/trigger_instances/manage/t_001" in str(request.url)
            return httpx.Response(200, json={"deleted": True})

        c = self._make_client(handler)
        result = await c.trigger_delete("t_001")
        assert result["deleted"] is True
        await c.close()


class TestAsyncAccountResolution:
    """Test the async _ensure_account_details flow."""

    _ACCOUNT_RESPONSE = {
        "id": "ca_test",
        "user_id": "user_123",
        "status": "ACTIVE",
        "deprecated": {"uuid": "00000000-0000-0000-0000-000000000001"},
    }

    async def test_resolves_uuid_from_deprecated(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if "/v3/connected_accounts/" in str(request.url) and request.method == "GET":
                return httpx.Response(200, json=self._ACCOUNT_RESPONSE)
            return httpx.Response(200, json={"ok": True})

        c = AsyncConnectorClient(
            proxy_url="https://proxy.test",
            api_key="test_api_key",
            connected_account_id="ca_test",
        )
        c._http = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), headers=c._http.headers
        )
        await c._ensure_account_details()
        assert c._resolved_uuid == "00000000-0000-0000-0000-000000000001"
        assert c._resolved_entity_id == "user_123"
        await c.close()

    async def test_caches_account_details(self) -> None:
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            if "/v3/connected_accounts/" in str(request.url):
                call_count += 1
                return httpx.Response(200, json=self._ACCOUNT_RESPONSE)
            return httpx.Response(200, json={"ok": True})

        c = AsyncConnectorClient(
            proxy_url="https://proxy.test",
            api_key="test_api_key",
            connected_account_id="ca_test",
        )
        c._http = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), headers=c._http.headers
        )
        await c._ensure_account_details()
        await c._ensure_account_details()
        assert call_count == 1  # Only called once, second time cached
        await c.close()
