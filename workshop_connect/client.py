"""Thin HTTP client for executing connector actions via the Workshop proxy.

Uses ``httpx`` for transport and ``Authorization: Bearer <api_key>``
for proxy authentication.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from .connector import Connector
from .errors import ActionError, AuthError

_USER_AGENT = "workshop-connect/0.1"
_TIMEOUT = 60.0
_HTTP_ERROR_THRESHOLD = 400


class ConnectorClient:
    """Synchronous connector client for the Workshop proxy.

    Preferred construction::

        client = ConnectorClient.from_connector("gmail")
        result = client.execute("GMAIL_GET_PROFILE", {})

    Or with an explicit prefix::

        client = ConnectorClient.from_env(prefix="MYSLCK")
    """

    def __init__(
        self,
        *,
        proxy_url: str,
        api_key: str,
        connected_account_id: str,
    ) -> None:
        if not api_key:
            raise AuthError("api_key is required")
        self._proxy_url = proxy_url.rstrip("/")
        self._api_key = api_key
        self._connected_account_id = connected_account_id
        self._resolved_uuid: str | None = None
        self._resolved_entity_id: str | None = None
        self._http = httpx.Client(
            timeout=_TIMEOUT,
            headers={
                "Authorization": f"Bearer {api_key}",
                "User-Agent": _USER_AGENT,
                "Content-Type": "application/json",
            },
        )

    def close(self) -> None:
        """Close the underlying connection pool."""
        self._http.close()

    def __enter__(self) -> ConnectorClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_connector(cls, name: str) -> ConnectorClient:
        """Resolve a connector by toolkit name and return a client.

        Scans the environment for a matching connector prefix
        and related env vars.
        """
        conn = Connector.from_env(toolkit=name)
        return cls(
            proxy_url=conn.proxy_url,
            api_key=conn.api_key,
            connected_account_id=conn.connected_account_id,
        )

    @classmethod
    def from_env(cls, prefix: str) -> ConnectorClient:
        """Resolve a connector by explicit prefix."""
        conn = Connector.from_env(prefix=prefix)
        return cls(
            proxy_url=conn.proxy_url,
            api_key=conn.api_key,
            connected_account_id=conn.connected_account_id,
        )

    # ------------------------------------------------------------------
    # Action execution
    # ------------------------------------------------------------------

    def execute(
        self,
        action: str,
        arguments: dict[str, Any] | None = None,
        *,
        entity_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute a connector action via the v3.1 tools API.

        Parameters
        ----------
        action:
            Action slug, e.g. ``"GMAIL_GET_PROFILE"``.
        arguments:
            Action parameters as a dict.
        entity_id:
            Optional entity ID override.  If omitted the client
            auto-resolves it from the connected-account metadata
            (``user_id`` field) on first call and caches it.

        Returns
        -------
        dict
            Parsed JSON response.

        Raises
        ------
        ActionError
            On HTTP 4xx/5xx from the proxy.
        """
        self._ensure_account_details()
        eid = entity_id or self._resolved_entity_id or ""
        url = f"{self._proxy_url}/v3.1/tools/execute/{action}"
        body: dict[str, Any] = {
            "connectedAccountId": self._resolved_uuid or self._connected_account_id,
            "entity_id": eid,
            "arguments": arguments or {},
        }
        resp = self._http.post(url, json=body)
        return self._handle_response(resp, action)

    # ------------------------------------------------------------------
    # Trigger management
    # ------------------------------------------------------------------

    def trigger_create(
        self,
        trigger_name: str,
        *,
        webhook_url: str,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a trigger subscription."""
        self._ensure_account_details()
        url = f"{self._proxy_url}/v3/triggers/subscribe/{trigger_name}"
        body: dict[str, Any] = {
            "connectedAccountId": self._resolved_uuid or self._connected_account_id,
            "triggerConfig": config or {},
            "webhookURL": webhook_url,
        }
        resp = self._http.post(url, json=body)
        return self._handle_response(resp, f"trigger_create:{trigger_name}")

    def trigger_list(self) -> list[dict[str, Any]]:
        """List active trigger instances."""
        self._ensure_account_details()
        url = f"{self._proxy_url}/v3/trigger_instances/active"
        params = {"connectedAccountId": self._resolved_uuid or self._connected_account_id}
        resp = self._http.get(url, params=params)
        data = self._handle_response(resp, "trigger_list")
        if isinstance(data, dict):
            instances = data.get("trigger_instances")
            if isinstance(instances, list):
                return instances
            return []
        if isinstance(data, list):
            return data
        return []

    def trigger_disable(self, trigger_id: str) -> dict[str, Any]:
        """Disable a trigger instance."""
        self._ensure_account_details()
        url = f"{self._proxy_url}/v3/trigger_instances/manage/{trigger_id}"
        params = {"connectedAccountId": self._resolved_uuid or self._connected_account_id}
        resp = self._http.patch(url, json={"enabled": False}, params=params)
        return self._handle_response(resp, f"trigger_disable:{trigger_id}")

    def trigger_delete(self, trigger_id: str) -> dict[str, Any]:
        """Delete a trigger instance."""
        self._ensure_account_details()
        url = f"{self._proxy_url}/v3/trigger_instances/manage/{trigger_id}"
        params = {"connectedAccountId": self._resolved_uuid or self._connected_account_id}
        resp = self._http.delete(url, params=params)
        return self._handle_response(resp, f"trigger_delete:{trigger_id}")

    # ------------------------------------------------------------------
    # Connection introspection
    # ------------------------------------------------------------------

    def connection_status(self) -> dict[str, Any]:
        """Check the connected account status."""
        url = f"{self._proxy_url}/v3/connected_accounts/{self._connected_account_id}"
        resp = self._http.get(url)
        return self._handle_response(resp, "connection_status")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _ensure_account_details(self) -> None:
        """Resolve account UUID and entity_id in one call (cached).

        Calls ``GET /v3/connected_accounts/{id}`` once to extract:
        - UUID from ``deprecated.uuid`` (v2 endpoints need this)
        - ``user_id`` → used as ``entity_id`` in execute calls
        """
        if self._resolved_uuid is not None:
            return
        aid = self._connected_account_id
        url = f"{self._proxy_url}/v3/connected_accounts/{aid}"
        resp = self._http.get(url)
        data = self._handle_response(resp, f"resolve_account:{aid}")
        if not isinstance(data, dict):
            data = {}
        self._resolved_uuid = (data.get("deprecated") or {}).get("uuid") or data.get(
            "id", aid
        )
        self._resolved_entity_id = data.get("user_id", "")

    @staticmethod
    def _handle_response(resp: httpx.Response, context: str) -> Any:
        if resp.status_code >= _HTTP_ERROR_THRESHOLD:
            body = resp.text[:500]
            raise ActionError(
                f"Action {context} failed ({resp.status_code}): {body}",
                status_code=resp.status_code,
                response_body=body,
            )
        try:
            return resp.json()
        except json.JSONDecodeError:
            return {"raw": resp.text}
