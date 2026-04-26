"""Connector resolution from environment variables.

Workshop connectors inject three env vars per connected account::

    <PREFIX>_COMPOSIO_PROXY_URL       — proxy base URL
    <PREFIX>_COMPOSIO_CONNECTED_ACCOUNT_ID  — Composio connected-account ID
    <PREFIX>_WORKSHOP_API_KEY         — Bearer token for the proxy

``Connector.from_env()`` scans ``os.environ`` for these patterns and
returns a resolved ``Connector`` dataclass ready for ``Composio``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .errors import AuthError, ConnectorNotFoundError

_PROXY_URL_SUFFIX = "_COMPOSIO_PROXY_URL"
_ACCOUNT_ID_SUFFIX = "_COMPOSIO_CONNECTED_ACCOUNT_ID"
_API_KEY_SUFFIX = "_WORKSHOP_API_KEY"
_APP_NAME_SUFFIX = "_COMPOSIO_APP_NAME"


@dataclass(frozen=True, slots=True)
class Connector:
    """Resolved connector credentials."""

    prefix: str
    proxy_url: str
    connected_account_id: str
    api_key: str

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_env(
        cls,
        prefix: str | None = None,
        *,
        toolkit: str | None = None,
    ) -> Connector:
        """Resolve a connector from environment variables.

        Parameters
        ----------
        prefix:
            Explicit prefix (e.g. ``"MYSLCK"``).  If given,
            looks up ``<PREFIX>_COMPOSIO_PROXY_URL`` etc. directly.
        toolkit:
            Composio toolkit slug (e.g. ``"gmail"``, ``"slack"``).
            When *prefix* is ``None``, scans the environment for a
            single prefix whose proxy URL is present, optionally
            filtering to those whose key includes the toolkit name.

        Returns
        -------
        Connector
            Resolved connector with all credentials populated.

        Raises
        ------
        ConnectorNotFoundError
            No matching connector found in the environment.
        AuthError
            Connector found but API key or account ID is missing.
        """
        if prefix is not None:
            return cls._resolve_prefix(prefix)
        return cls._auto_detect(toolkit)

    # ------------------------------------------------------------------
    # Internal resolution
    # ------------------------------------------------------------------

    @classmethod
    def _resolve_prefix(cls, prefix: str) -> Connector:
        prefix = prefix.upper()
        proxy_url = os.environ.get(f"{prefix}{_PROXY_URL_SUFFIX}", "")
        if not proxy_url:
            raise ConnectorNotFoundError(
                f"No connector with prefix {prefix!r}: "
                f"env var {prefix}{_PROXY_URL_SUFFIX} not set."
            )
        account_id = os.environ.get(f"{prefix}{_ACCOUNT_ID_SUFFIX}", "")
        api_key = os.environ.get(f"{prefix}{_API_KEY_SUFFIX}", "")
        if not api_key:
            raise AuthError(
                f"Connector {prefix!r} found but {prefix}{_API_KEY_SUFFIX} is not set."
            )
        if not account_id:
            raise AuthError(
                f"Connector {prefix!r} found but "
                f"{prefix}{_ACCOUNT_ID_SUFFIX} is not set."
            )
        return cls(
            prefix=prefix,
            proxy_url=proxy_url.rstrip("/"),
            connected_account_id=account_id,
            api_key=api_key,
        )

    @classmethod
    def _auto_detect(cls, toolkit: str | None) -> Connector:
        """Scan env for *_COMPOSIO_PROXY_URL keys and return the match."""
        candidates: list[str] = []
        for key in os.environ:
            if key.endswith(_PROXY_URL_SUFFIX):
                pfx = key[: -len(_PROXY_URL_SUFFIX)]
                if pfx:
                    candidates.append(pfx)

        if not candidates:
            raise ConnectorNotFoundError(
                "No connector found in environment. "
                "Expected at least one <PREFIX>_COMPOSIO_PROXY_URL variable."
            )

        if toolkit:
            tk_norm = toolkit.lower().replace("-", "").replace("_", "")

            # Strategy 1: match via {PREFIX}_COMPOSIO_APP_NAME (reliable)
            by_app = [
                p
                for p in candidates
                if os.environ.get(f"{p}{_APP_NAME_SUFFIX}", "")
                .lower()
                .replace("-", "")
                .replace("_", "")
                == tk_norm
            ]
            if len(by_app) == 1:
                return cls._resolve_prefix(by_app[0])

            # Strategy 2: fallback — prefix string contains toolkit name
            tk_upper = toolkit.upper().replace("-", "").replace("_", "")
            by_name = [p for p in candidates if tk_upper in p.replace("_", "")]
            if len(by_name) == 1:
                return cls._resolve_prefix(by_name[0])
            if by_name:
                candidates = by_name

        if len(candidates) == 1:
            return cls._resolve_prefix(candidates[0])

        # Build a helpful message showing prefix→app mapping
        mapping_parts = []
        for p in candidates:
            app = os.environ.get(f"{p}{_APP_NAME_SUFFIX}", "?")
            mapping_parts.append(f"{p} ({app})")
        raise ConnectorNotFoundError(
            f"Multiple connectors found: {', '.join(mapping_parts)}. "
            f"Use --prefix <PREFIX> to select one."
        )
