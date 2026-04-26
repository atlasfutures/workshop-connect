"""Workshop connector SDK.

Thin client for executing connector actions and managing triggers
through the Workshop proxy.

Public API
----------
- ``ConnectorClient``      — synchronous HTTP client
- ``AsyncConnectorClient`` — async HTTP client (FastAPI, asyncio)
- ``Connector``            — prefix-based connector resolution from env
- ``ConnectorError``       — base error class
"""

from __future__ import annotations

from .async_client import AsyncConnectorClient
from .client import ConnectorClient
from .connector import Connector
from .errors import ConnectorError

__all__ = ["AsyncConnectorClient", "ConnectorClient", "Connector", "ConnectorError"]
