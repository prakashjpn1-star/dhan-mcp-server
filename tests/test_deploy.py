from starlette.testclient import TestClient
from dhan_mcp_server.deploy import create_app

def test_missing_credentials_fail_closed(monkeypatch):
    for key in ("DHAN_ACCESS_TOKEN", "DHAN_ACCESS_TOKEN_FILE", "DHAN_CLIENT_ID",
                "DHAN_CLIENT_ID_FILE", "MCP_OAUTH_ISSUER", "MCP_JWKS_URL",
                "MCP_RESOURCE_URL", "MCP_ALLOWED_SUBJECT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("dhan_mcp_server.deploy.load_dotenv", lambda: None)
    with TestClient(create_app()) as client:
        assert client.get("/health").json()["mcp_enabled"] is False
        assert client.post("/mcp", json={"method": "tools/list"}).status_code == 503
