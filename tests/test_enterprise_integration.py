"""Enterprise multi-user integration tests — end-to-end."""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from qwenpaw.enterprise import set_providers, clear_providers
from qwenpaw.enterprise.context import UserInfo, get_current_user, set_current_user
from qwenpaw.enterprise.auth import ExternalAuthProvider, ExternalUserResolver
from qwenpaw.enterprise.permissions import ExternalPermissionProvider


@pytest.fixture(autouse=True)
def reset_providers():
    clear_providers()
    yield
    clear_providers()


class TestEnterpriseIntegration:
    """Full integration: auth + permissions + context flow."""

    @pytest.mark.asyncio
    async def test_http_api_auth_flow(self):
        """Simulate HTTP API request flow from auth to tool execution."""
        # 1. Set up providers (simulating _app.py startup)
        set_providers(
            ExternalAuthProvider(service_url="http://auth:8080"),
            None,  # UserResolver not needed for HTTP path
            ExternalPermissionProvider(service_url="http://rbac:8080"),
        )

        # 2. Simulate AuthMiddleware — authenticate user
        from qwenpaw.enterprise import get_auth_provider

        mock_request = AsyncMock()
        mock_request.headers = {"x-auth-token": "token-xyz"}

        auth_response = MagicMock()
        auth_response.status_code = 200
        auth_response.json.return_value = {
            "user_id": "ent-user-3",
            "org_id": "org-engineering",
            "roles": ["developer"],
            "permissions": ["tool:read_file", "tool:write_file"],
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post.return_value = auth_response
            auth_provider = get_auth_provider()
            user = await auth_provider.authenticate(mock_request)

        assert user is not None
        assert user.user_id == "ent-user-3"
        set_current_user(user)

        # 3. Simulate tool execution — permission check
        from qwenpaw.enterprise import get_permission_provider

        perm_response_allowed = MagicMock()
        perm_response_allowed.json.return_value = {"allowed": True}

        perm_response_denied = MagicMock()
        perm_response_denied.json.return_value = {"allowed": False}

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post.return_value = perm_response_allowed
            perm_provider = get_permission_provider()
            allowed = await perm_provider.check_permission(
                user, "tool:read_file", "read_file",
            )
        assert allowed is True

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post.return_value = perm_response_denied
            denied = await perm_provider.check_permission(
                user, "tool:execute_shell_command", "execute_shell_command",
            )
        assert denied is False

    @pytest.mark.asyncio
    async def test_channel_message_resolve_flow(self):
        """Simulate DingTalk channel message -> user resolution -> permission check."""
        # 1. Set up providers with UserResolver
        set_providers(
            None,  # No HTTP auth provider (channel messages don't use Bearer tokens)
            ExternalUserResolver(service_url="http://auth:8080"),
            ExternalPermissionProvider(service_url="http://rbac:8080"),
        )

        # 2. Simulate channel receiving a message from DingTalk user
        from qwenpaw.enterprise import get_user_resolver

        resolve_response = MagicMock()
        resolve_response.status_code = 200
        resolve_response.json.return_value = {
            "user_id": "ent-user-4",
            "org_id": "org-marketing",
            "roles": ["manager"],
            "permissions": [],
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post.return_value = resolve_response
            resolver = get_user_resolver()
            user = await resolver.resolve("dingtalk", "dingtalk-user-456")

        assert user is not None
        assert user.user_id == "ent-user-4"
        set_current_user(user)

        # 3. Check permission for the resolved user
        from qwenpaw.enterprise import get_permission_provider

        perm_response = MagicMock()
        perm_response.json.return_value = {"allowed": True}

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post.return_value = perm_response
            perm_provider = get_permission_provider()
            allowed = await perm_provider.check_permission(
                user, "agent:use", "default",
            )

        assert allowed is True

    def test_disabled_enterprise_has_no_effect(self):
        """When enterprise is not enabled, everything passes through."""
        # No providers set (default state)
        from qwenpaw.enterprise import get_auth_provider, get_permission_provider

        assert get_auth_provider() is None
        assert get_permission_provider() is None
        assert get_current_user() is None