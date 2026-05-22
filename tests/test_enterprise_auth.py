from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from qwenpaw.enterprise.context import UserInfo
from qwenpaw.enterprise.auth import (
    ExternalAuthProvider,
    ExternalUserResolver,
    NoopAuthProvider,
)


class TestNoopAuthProvider:
    @pytest.mark.asyncio
    async def test_returns_none(self):
        provider = NoopAuthProvider()
        result = await provider.authenticate(None)
        assert result is None


class TestExternalAuthProvider:
    @pytest.mark.asyncio
    async def test_authenticate_success(self):
        provider = ExternalAuthProvider(
            service_url="http://auth:8080",
            token_header="X-Auth-Token",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "user_id": "ent-user-1",
            "org_id": "org-1",
            "roles": ["employee"],
            "permissions": ["tool:read_file"],
        }

        mock_request = MagicMock()
        mock_request.headers = {"x-auth-token": "token-abc"}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            result = await provider.authenticate(mock_request)

        assert result is not None
        assert result.user_id == "ent-user-1"
        assert result.org_id == "org-1"
        assert result.roles == ["employee"]

    @pytest.mark.asyncio
    async def test_authenticate_no_token(self):
        provider = ExternalAuthProvider(service_url="http://auth:8080")

        mock_request = MagicMock()
        mock_request.headers = {}

        result = await provider.authenticate(mock_request)
        assert result is None

    @pytest.mark.asyncio
    async def test_authenticate_service_error(self):
        provider = ExternalAuthProvider(service_url="http://auth:8080")

        mock_request = MagicMock()
        mock_request.headers = {"x-auth-token": "token-abc"}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.side_effect = OSError("connection refused")

            result = await provider.authenticate(mock_request)

        assert result is None


class TestExternalUserResolver:
    @pytest.mark.asyncio
    async def test_resolve_success(self):
        resolver = ExternalUserResolver(service_url="http://auth:8080")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "user_id": "ent-user-2",
            "org_id": "org-2",
            "roles": ["manager"],
            "permissions": [],
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            result = await resolver.resolve("dingtalk", "dt-user-123")

        assert result is not None
        assert result.user_id == "ent-user-2"
        assert result.org_id == "org-2"

    @pytest.mark.asyncio
    async def test_resolve_unknown_user(self):
        resolver = ExternalUserResolver(service_url="http://auth:8080")

        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            result = await resolver.resolve("dingtalk", "unknown-user")

        assert result is None
