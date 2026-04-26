"""Deployment-time connector resolution.

When a Workshop app is deployed (published), the connector env vars
injected during development are not available.  Instead, the deployed
container receives:

- ``WORKSHOP_DEPLOYMENT_TOKEN`` — JWT authorizing access to user's connectors
- ``WORKSHOP_BACKEND_URL`` — Workshop backend API base URL
- ``{PREFIX}_CONNECTOR_ID`` — Firestore connector document ID (per connector)

This module resolves Composio connector credentials at runtime by
calling the Workshop backend, analogous to how ``secrets_utils.py``
resolves OAuth access tokens for native connectors.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from .errors import AuthError, ConnectorNotFoundError

_DEPLOYMENT_TOKEN_VAR = "WORKSHOP_DEPLOYMENT_TOKEN"
_BACKEND_URL_VAR = "WORKSHOP_BACKEND_URL"
_CONNECTOR_ID_SUFFIX = "_CONNECTOR_ID"
_APP_NAME_SUFFIX = "_COMPOSIO_APP_NAME"
_TIMEOUT = 30.0


def is_deployment() -> bool:
    """Return True if running inside a deployed Workshop app."""
    return bool(
        os.environ.get(_DEPLOYMENT_TOKEN_VAR)
        and os.environ.get(_BACKEND_URL_VAR)
    )


def resolve_composio_credentials(
    *,
    connector_id: str | None = None,
    toolkit: str | None = None,
) -> dict[str, Any]:
    """Resolve Composio credentials via the Workshop backend.

    Parameters
    ----------
    connector_id:
        The Firestore connector document ID.  Takes priority if given.
    toolkit:
        Composio toolkit slug (e.g. ``"gmail"``).  Used to scan
        ``{PREFIX}_CONNECTOR_ID`` env vars when *connector_id* is None.

    Returns
    -------
    dict
        ``{"proxy_url", "api_key", "connected_account_id", "app_name"}``

    Raises
    ------
    ConnectorNotFoundError
        No deployment context or no matching connector found.
    AuthError
        Deployment token is present but the backend rejected it.
    """
    token = os.environ.get(_DEPLOYMENT_TOKEN_VAR, "")
    backend_url = os.environ.get(_BACKEND_URL_VAR, "")
    if not token or not backend_url:
        raise ConnectorNotFoundError(
            "Not in a deployment context "
            f"({_DEPLOYMENT_TOKEN_VAR} or {_BACKEND_URL_VAR} not set)."
        )

    # Resolve connector_id from env if not given directly.
    if connector_id is None:
        connector_id = _find_connector_id(toolkit)

    url = f"{backend_url.rstrip('/')}/deployments/connectors/{connector_id}/composio_credentials"
    try:
        resp = httpx.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=_TIMEOUT,
        )
    except httpx.RequestError as exc:
        raise ConnectorNotFoundError(
            f"Failed to reach Workshop backend at {backend_url}: {exc}"
        ) from exc

    if resp.status_code == 401:
        raise AuthError(
            "Deployment token rejected by Workshop backend. "
            "It may have expired — redeploy the app to get a fresh token."
        )
    if resp.status_code == 403:
        raise AuthError(
            f"Connector {connector_id} is not authorized for this deployment."
        )
    if resp.status_code == 404:
        raise ConnectorNotFoundError(
            f"Connector {connector_id} not found. "
            "Ensure the connector is linked to this project."
        )
    if resp.status_code >= 400:
        raise ConnectorNotFoundError(
            f"Backend returned {resp.status_code}: {resp.text[:200]}"
        )

    return resp.json()


def _find_connector_id(toolkit: str | None) -> str:
    """Scan env for {PREFIX}_CONNECTOR_ID matching the toolkit."""
    candidates: list[tuple[str, str]] = []  # (prefix, connector_id)
    for key, val in os.environ.items():
        if key.endswith(_CONNECTOR_ID_SUFFIX) and val:
            prefix = key[: -len(_CONNECTOR_ID_SUFFIX)]
            if prefix:
                candidates.append((prefix, val))

    if not candidates:
        raise ConnectorNotFoundError(
            "No {PREFIX}_CONNECTOR_ID env vars found. "
            "Ensure the connector is linked to this project's deployment."
        )

    if toolkit:
        tk_norm = toolkit.lower().replace("-", "").replace("_", "")
        # Match via {PREFIX}_COMPOSIO_APP_NAME
        for prefix, cid in candidates:
            app = os.environ.get(f"{prefix}{_APP_NAME_SUFFIX}", "")
            if app.lower().replace("-", "").replace("_", "") == tk_norm:
                return cid
        # Fallback: prefix contains toolkit name
        tk_upper = toolkit.upper().replace("-", "").replace("_", "")
        for prefix, cid in candidates:
            if tk_upper in prefix.replace("_", ""):
                return cid

    if len(candidates) == 1:
        return candidates[0][1]

    labels = [f"{p} ({os.environ.get(f'{p}{_APP_NAME_SUFFIX}', '?')})" for p, _ in candidates]
    raise ConnectorNotFoundError(
        f"Multiple connectors found: {', '.join(labels)}. "
        "Pass connector_id explicitly."
    )
