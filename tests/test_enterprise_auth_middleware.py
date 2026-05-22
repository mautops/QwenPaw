"""Integration test: AuthMiddleware + UserContext flow."""
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from qwenpaw.enterprise.context import (
    EnterpriseMiddleware, get_current_user, set_current_user, UserInfo,
)
from qwenpaw.enterprise.auth import ExternalAuthProvider
from qwenpaw.enterprise import set_providers, clear_providers


class FakeAuthMiddleware(BaseHTTPMiddleware):
    """Simulate QwenPaw's AuthMiddleware behaviour for integration test."""

    async def dispatch(self, request: Request, call_next):
        # Simulate: token validated, user set on request.state
        request.state.user = "local-user"

        from qwenpaw.enterprise import get_auth_provider
        auth_provider = get_auth_provider()
        if auth_provider is not None:
            try:
                enterprise_user = await auth_provider.authenticate(request)
                if enterprise_user is not None:
                    set_current_user(enterprise_user)
                    request.state.user = enterprise_user.user_id
            except Exception:
                pass

        return await call_next(request)


def test_user_context_flow_with_enterprise_enabled():
    clear_providers()

    # Set up external auth provider
    mock_auth = ExternalAuthProvider(service_url="http://auth:8080")
    set_providers(mock_auth, None, None)

    app = FastAPI()
    app.add_middleware(EnterpriseMiddleware)
    app.add_middleware(FakeAuthMiddleware)

    @app.get("/api/test")
    def handler(request: Request):
        user = get_current_user()
        return {
            "request_user": request.state.user,
            "context_user_id": user.user_id if user else None,
        }

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "user_id": "ent-user-1",
        "org_id": "org-1",
        "roles": ["employee"],
        "permissions": [],
    }

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_response

        client = TestClient(app)
        response = client.get("/api/test", headers={"x-auth-token": "tok"})

    assert response.status_code == 200
    body = response.json()
    assert body["request_user"] == "ent-user-1"
    assert body["context_user_id"] == "ent-user-1"

    clear_providers()


def test_user_context_flow_without_enterprise():
    clear_providers()

    app = FastAPI()
    app.add_middleware(EnterpriseMiddleware)
    app.add_middleware(FakeAuthMiddleware)

    @app.get("/api/test")
    def handler(request: Request):
        user = get_current_user()
        return {
            "request_user": request.state.user,
            "context_user_id": user.user_id if user else None,
        }

    client = TestClient(app)
    response = client.get("/api/test")

    assert response.status_code == 200
    body = response.json()
    assert body["request_user"] == "local-user"
    assert body["context_user_id"] is None

    clear_providers()