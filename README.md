# workshop-connect

Python SDK for [Workshop.ai](https://workshop.ai) connectors — execute actions and manage triggers through the Workshop proxy.

## Installation

```bash
pip install workshop-connect
```

For the CLI tool:

```bash
pip install workshop-connect[cli]
```

## Quick Start

### Python SDK

```python
from workshop_connect import ConnectorClient

# Auto-detect connector from environment variables
client = ConnectorClient.from_connector("gmail")
result = client.execute("GMAIL_GET_PROFILE", {"userId": "me"})
print(result)

# Or with an explicit prefix
client = ConnectorClient.from_env(prefix="MYGMAL")
```

### Async SDK (for FastAPI / async apps)

```python
from workshop_connect import AsyncConnectorClient

async with AsyncConnectorClient.from_connector("gmail") as client:
    result = await client.execute("GMAIL_GET_PROFILE", {"userId": "me"})
```

### FastAPI Integration

```python
from fastapi import FastAPI
from workshop_connect import AsyncConnectorClient

app = FastAPI()

@app.get("/api/emails")
async def list_emails():
    async with AsyncConnectorClient.from_connector("gmail") as client:
        return await client.execute("GMAIL_LIST_EMAILS", {"maxResults": 10})
```

## Environment Variables

Each connected account injects three environment variables:

```
<PREFIX>_COMPOSIO_PROXY_URL              # Workshop proxy URL
<PREFIX>_COMPOSIO_CONNECTED_ACCOUNT_ID   # Connected account ID
<PREFIX>_WORKSHOP_API_KEY                # Bearer token
<PREFIX>_COMPOSIO_APP_NAME               # Toolkit name (e.g. "gmail")
```

## License

Apache-2.0
