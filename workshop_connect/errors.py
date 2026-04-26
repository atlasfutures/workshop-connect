"""Error hierarchy for workshop-connect."""

from __future__ import annotations


class ConnectorError(Exception):
    """Base error for all workshop-connect failures."""


class AuthError(ConnectorError):
    """Missing or invalid authentication credentials."""


class ConnectorNotFoundError(ConnectorError):
    """No matching connector found in environment."""


class ActionError(ConnectorError):
    """Connector action execution failed."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 0,
        response_body: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class CatalogError(ConnectorError):
    """Catalog loading or lookup failure."""
