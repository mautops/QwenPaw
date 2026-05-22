from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from qwenpaw.enterprise.context import UserInfo
from qwenpaw.enterprise.permissions import (
    NoopPermissionProvider,
    ExternalPermissionProvider,
)


class TestNoopPermissionProvider:
    @pytest.mark.asyncio
    async def test_always_allows(self):
        provider = NoopPermissionProvider()
        user = UserInfo(user_id="u1")
        result = await provider.check_permission(user, "any:action", "any_resource")
        assert result is True


class TestExternalPermissionProvider:
    @pytest.mark.asyncio
    async def test_check_permission_allowed(self):
        provider = ExternalPermissionProvider(
            service_url="http://rbac:8080", timeout=1.0,
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"allowed": True}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            user = UserInfo(user_id="u1", org_id="org-1")
            result = await provider.check_permission(
                user, "tool:execute_shell_command", "execute_shell_command",
            )

        assert result is True

    @pytest.mark.asyncio
    async def test_check_permission_denied(self):
        provider = ExternalPermissionProvider(service_url="http://rbac:8080")

        mock_response = MagicMock()
        mock_response.json.return_value = {"allowed": False}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            user = UserInfo(user_id="u1")
            result = await provider.check_permission(user, "agent:delete", "agent-1")

        assert result is False

    @pytest.mark.asyncio
    async def test_check_permission_service_unreachable_fail_closed(self):
        provider = ExternalPermissionProvider(service_url="http://rbac:8080")

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.side_effect = OSError("connection refused")

            user = UserInfo(user_id="u1")
            result = await provider.check_permission(user, "tool:shell", "shell")

        assert result is False

    @pytest.mark.asyncio
    async def test_posts_correct_payload(self):
        provider = ExternalPermissionProvider(service_url="http://rbac:8080")

        mock_response = MagicMock()
        mock_response.json.return_value = {"allowed": True}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            user = UserInfo(
                user_id="u1",
                org_id="org-1",
                roles=["admin"],
                permissions=["tool:shell"],
            )
            await provider.check_permission(user, "tool:shell", "shell")

            call_args = mock_client.post.call_args
            url = call_args[0][0]
            body = call_args[1]["json"]

            assert url == "http://rbac:8080/api/rbac/check"
            assert body["user_id"] == "u1"
            assert body["org_id"] == "org-1"
            assert body["roles"] == ["admin"]
            assert body["action"] == "tool:shell"
            assert body["resource"] == "shell"
