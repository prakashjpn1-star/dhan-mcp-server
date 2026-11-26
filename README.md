# Dhan MCP Server — read-only remote integration

Python 3.11+ MCP server for one Dhan account. Supports STDIO for local clients and Streamable HTTP for remote clients. No trading mutation tools or generic HTTP proxy are exposed. Do not deploy an unauthenticated remote instance.

## Setup

Create a virtual environment, activate it, and run `python -m pip install ".[dev]"`. On Windows use `py -3.12 -m venv .venv` and `.venv\Scripts\activate`; on Linux use `python3 -m venv .venv` and `source .venv/bin/activate`. Copy .env.example to .env (not tracked) and provide Dhan client ID and access token obtained from Dhan. Alternatively set DHAN_CLIENT_ID_FILE and DHAN_ACCESS_TOKEN_FILE to mounted secret files, which take precedence over environment variables and are reread per broker request. Rotate broker credentials outside MCP; there is no token generation tool. Never paste credentials into chat or tool arguments.

Local: `python -m dhan_mcp_server --transport stdio`. For local MCP clients configure the executable as the virtual environment Python and arguments as `["-m","dhan_mcp_server","--transport","stdio"]`. The process environment is the trust boundary. Windows and Unix launch scripts are included. No stdout logging outside MCP protocol.

## Remote OAuth and ChatGPT

Use a managed or self-hosted OAuth authorization server, e.g. an appropriately configured identity provider. This repository is the OAuth resource server, not an authorization server. The provider must expose authorization-server metadata, authorization code flow with S256 PKCE, client registration compatible with your ChatGPT configuration (dynamic registration or pre-registered client), and RS256 JWT access tokens with audience equal to the full MCP resource URL. Register the exact callback URL shown by ChatGPT and configure its requested scopes. Enable MFA for the owner account. Verify the provider supports resource indicators/audience issuance for the MCP endpoint; an ordinary OIDC ID token is not a substitute for an access token.

Set:
- MCP_OAUTH_ISSUER: exact issuer HTTPS URL as in the access token
- MCP_JWKS_URL: trusted HTTPS JWKS endpoint
- MCP_RESOURCE_URL: public HTTPS URL ending /mcp, exactly matching token aud
- MCP_ALLOWED_SUBJECT: immutable owner subject from the provider (single-account isolation)
- MCP_REQUIRED_SCOPE: default dhan:read

Run `python -m dhan_mcp_server --transport streamable-http`. Terminate TLS at a reverse proxy, preserve Host, do not rewrite /mcp or /.well-known routes, and forward Authorization. Restrict direct port access. Register the public /mcp URL in ChatGPT's custom MCP app configuration and select OAuth; follow the provider's sign-in and consent flow. The SDK serves protected-resource metadata and bearer challenges. Test an unauthenticated request returns 401 and discovery points at the configured issuer before connecting. A 403 can indicate missing scope, incorrect audience/subject or Host/Origin policy. Verify provider configuration and logs without logging tokens. Actual ChatGPT connection requires external OAuth deployment and live validation; no provider account has been provisioned by this project.

## Tools

search_instruments (symbol/name/security ID), get_market_ltp, get_market_ohlc, get_market_depth, get_historical_data, get_intraday_data, get_holdings, get_positions, get_fund_limits, validate_token. All advertise read-only annotations; the underlying client independently rejects non-allowlisted paths.

For UGRO Capital, search for UGROCAP, select NSE equity ID, then invoke get_market_ltp with instruments {"NSE_EQ":[resolved ID]}. Do not guess security IDs. Market-data subscriptions and Dhan API limits apply. Live quote snapshots are cached for two seconds; historical candles for five minutes; instrument master for six hours. Portfolio data is never cached. Requests are serialized and paced, with bounded retries on transient errors and 429. All broker calls use HTTPS and fixed upstream URLs. Broker tokens never serve as MCP tokens.

## Deployment

Build `docker build -t dhan-mcp .`. Run behind an HTTPS proxy: `docker run --rm --env-file .env -p 127.0.0.1:8000:8000 dhan-mcp`. For a production deployment mount secret files read-only and configure the OAuth variables separately. Keep a single worker/replica per Dhan account because rate limits and cache are process-local. Restrict container and proxy logs to avoid sensitive request content. Do not share the endpoint with other users. JWT authorization changes take effect when tokens expire; use short-lived tokens and provider revocation controls. A compromised broker token must be revoked at Dhan.

## Tests

`python -m pytest -q` uses synthetic credentials, mocked Dhan HTTP responses and locally generated RSA keys. CI runs on Linux and Windows and builds the Docker image. No tests should place orders. Verify real quotes, data subscription, holdings, TLS and OAuth end-to-end separately before production.

## API references

- https://dhanhq.co/docs/v2/
- https://dhanhq.co/docs/v2/instruments/
- https://modelcontextprotocol.io/specification/
- https://github.com/modelcontextprotocol/python-sdk

The old trading server interface has intentionally been removed from the entry point. This is a breaking change for clients that relied on order tools.

## Render hosting

Create a Docker web service from this branch. The default image command starts the ASGI service on Render's PORT. Until both Dhan credentials and all OAuth settings are present, /health reports configuration_required and /mcp returns HTTP 503; no broker tools are exposed. Set secrets in the Render dashboard, not in source control. Set MCP_RESOURCE_URL to the actual service HTTPS URL followed by /mcp. Redeploy after setting the values. The ready health response confirms configuration presence, not successful live broker or OAuth authentication.

## Optional free Upstox market-data provider

Dhan portfolio tools remain unchanged. For live market data without a Dhan Data API subscription, generate the free read-only Analytics Token at https://account.upstox.com/developer/apps (Analytics tab; see https://upstox.com/developer/api-documentation/analytics-token/). Add UPSTOX_ANALYTICS_TOKEN in Render Environment or mount UPSTOX_ANALYTICS_TOKEN_FILE. No token is stored in Git. Upstox account required. Use upstox_search_instruments followed by upstox_get_quotes, upstox_get_history or upstox_get_intraday. The provider is explicit; Dhan instrument IDs are not substituted for Upstox keys. The returned fetched_at_utc is retrieval time, not exchange trade time. All calls are GET, cached, paced and retried for transient failures. The public MCP endpoint remains OAuth protected. Market access must be verified with a real analytics token.
