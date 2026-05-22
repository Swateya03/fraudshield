"""
tests/test_contract.py
───────────────────────
OpenAPI contract tests using schemathesis.
Runs every declared endpoint against the live schema and validates:
  - HTTP status codes are within the spec-declared set
  - Response bodies match the declared response schema
  - No 5xx errors on valid-schema inputs

Run standalone:
  pytest tests/test_contract.py -v

CI: see .github/workflows/test.yml (contract-tests job).
"""

import pytest
import schemathesis
from starlette.testclient import TestClient

from fraud_api.main import app
from fraudshield_core import config as _config

# Override auth token so every generated request is authenticated.
_AUTH = {"X-API-Token": "test_token_123"}

# Load schema from the ASGI app directly — no network required.
schema = schemathesis.from_asgi("/openapi.json", app)


@schema.parametrize()
def test_api_contract(case):
    """
    schemathesis generates valid inputs for every (endpoint, method, status) triple
    declared in the OpenAPI schema and asserts the response conforms.
    """
    client = TestClient(app, raise_server_exceptions=False)
    response = case.call_wsgi(client, headers=_AUTH)
    case.validate_response(response)


# ── Stateful / link-following contract test ──────────────────────────────────

stateful_schema = schemathesis.from_asgi(
    "/openapi.json",
    app,
    stateful=schemathesis.Stateful.links,
)


@stateful_schema.parametrize()
def test_api_stateful(case):
    """
    Follows OpenAPI response links (if defined) to build multi-step call chains,
    e.g. POST /v1/score → GET /v1/transactions/{id}.
    """
    client = TestClient(app, raise_server_exceptions=False)
    response = case.call_wsgi(client, headers=_AUTH)
    case.validate_response(response)
