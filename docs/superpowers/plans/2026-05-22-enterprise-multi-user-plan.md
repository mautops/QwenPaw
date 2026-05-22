# Enterprise Multi-User System — QwenPaw 本体改造实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 QwenPaw 本体中建立企业多用户扩展框架——用户上下文传递、可插拔认证/权限、工具执行鉴权，最小侵入且不影响上游合并。

**Architecture:** 新建 `src/qwenpaw/enterprise/` 独立目录包含 5 个模块，改动 6 个现有文件。通过 Protocol + 工厂函数实现可插拔扩展点，`enterprise.enabled=false` 时零影响。企业认证和权限服务作为外部 HTTP 微服务。

**Tech Stack:** Python 3.12+, FastAPI/Starlette, Pydantic v2, httpx, contextvars

---

## 文件结构

```
新增:
  src/qwenpaw/enterprise/__init__.py          # 工厂函数 + 模块单例
  src/qwenpaw/enterprise/config.py             # EnterpriseConfig Pydantic 模型
  src/qwenpaw/enterprise/context.py            # UserInfo + UserContext + EnterpriseMiddleware
  src/qwenpaw/enterprise/auth.py               # AuthProvider / UserResolver 协议 + 实现
  src/qwenpaw/enterprise/permissions.py         # PermissionProvider 协议 + 实现
  tests/test_enterprise_config.py              # 配置模型测试
  tests/test_enterprise_context.py             # 上下文和中间件测试
  tests/test_enterprise_auth.py                # 认证提供者测试
  tests/test_enterprise_permissions.py         # 权限提供者测试

修改:
  src/qwenpaw/config/config.py                 # +enterprise 字段
  src/qwenpaw/app/auth.py                      # +UserContext 设置
  src/qwenpaw/app/_app.py                      # +中间件注册 + 初始化/销毁
  src/qwenpaw/agents/tool_guard_mixin.py        # +权限检查
  src/qwenpaw/app/routers/agents.py             # +Agent 管理鉴权
```

---

### Task 1: Enterprise 配置模型

**Files:**
- Create: `src/qwenpaw/enterprise/__init__.py`
- Create: `src/qwenpaw/enterprise/config.py`
- Create: `tests/test_enterprise_config.py`
- Modify: `src/qwenpaw/config/config.py`

- [ ] **Step 1: Create enterprise package `__init__.py`**

`src/qwenpaw/enterprise/__init__.py`:
```python
"""企业功能扩展模块。"""

_auth_provider = None
_user_resolver = None
_permission_provider = None

def set_providers(auth_provider, user_resolver, permission_provider) -> None:
    global _auth_provider, _user_resolver, _permission_provider
    _auth_provider = auth_provider
    _user_resolver = user_resolver
    _permission_provider = permission_provider

def clear_providers() -> None:
    set_providers(None, None, None)

def get_auth_provider():
    return _auth_provider

def get_user_resolver():
    return _user_resolver

def get_permission_provider():
    return _permission_provider

def is_enterprise_mode() -> bool:
    return _auth_provider is not None
```

- [ ] **Step 2: Create EnterpriseConfig 模型**

`src/qwenpaw/enterprise/config.py`:
```python
from pydantic import BaseModel, Field

class EnterpriseAuthConfig(BaseModel):
    mode: str = "local"
    service_url: str = ""
    token_header: str = "X-Auth-Token"

class EnterpriseRbacConfig(BaseModel):
    service_url: str = ""

class EnterpriseConfig(BaseModel):
    enabled: bool = False
    auth: EnterpriseAuthConfig = Field(default_factory=EnterpriseAuthConfig)
    rbac: EnterpriseRbacConfig = Field(default_factory=EnterpriseRbacConfig)
```

- [ ] **Step 3: Write config model tests**

`tests/test_enterprise_config.py`:
```python
import json
from qwenpaw.enterprise.config import EnterpriseConfig, EnterpriseAuthConfig, EnterpriseRbacConfig


def test_enterprise_config_defaults():
    cfg = EnterpriseConfig()
    assert cfg.enabled is False
    assert cfg.auth.mode == "local"
    assert cfg.auth.service_url == ""
    assert cfg.rbac.service_url == ""


def test_enterprise_config_enabled_with_external_auth():
    cfg = EnterpriseConfig(
        enabled=True,
        auth=EnterpriseAuthConfig(
            mode="external",
            service_url="http://auth-service:8080",
        ),
        rbac=EnterpriseRbacConfig(
            service_url="http://rbac-service:8080",
        ),
    )
    assert cfg.enabled is True
    assert cfg.auth.mode == "external"
    assert cfg.auth.service_url == "http://auth-service:8080"
    assert cfg.rbac.service_url == "http://rbac-service:8080"


def test_enterprise_config_serialization():
    cfg = EnterpriseConfig(enabled=True)
    data = cfg.model_dump()
    assert data["enabled"] is True
    # Round-trip
    cfg2 = EnterpriseConfig(**data)
    assert cfg2.enabled is True
```

Run: `pytest tests/test_enterprise_config.py -v`
Expected: 3 tests PASS

- [ ] **Step 4: Add enterprise field to root Config**

In `src/qwenpaw/config/config.py`, add after `user_timezone` field (approximately line 1754, inside `Config` class):

```python
enterprise: EnterpriseConfig = Field(
    default_factory=EnterpriseConfig,
    description="Enterprise multi-user features configuration",
)
```

And add the import at the top of the file:
```python
from qwenpaw.enterprise.config import EnterpriseConfig
```

Run: `pytest tests/test_enterprise_config.py -v`
Expected: 3 tests still PASS (import chain works)

- [ ] **Step 5: Commit**

```bash
git add src/qwenpaw/enterprise/__init__.py \
        src/qwenpaw/enterprise/config.py \
        tests/test_enterprise_config.py \
        src/qwenpaw/config/config.py
git commit -m "feat(enterprise): add EnterpriseConfig model and root config integration"
```

---

### Task 2: UserContext 和 EnterpriseMiddleware

**Files:**
- Create: `src/qwenpaw/enterprise/context.py`
- Create: `tests/test_enterprise_context.py`

- [ ] **Step 1: Write context test (failing)**

`tests/test_enterprise_context.py`:
```python
import asyncio
from unittest.mock import AsyncMock, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient

from qwenpaw.enterprise.context import (
    UserInfo,
    get_current_user,
    set_current_user,
    EnterpriseMiddleware,
)


class TestUserInfo:
    def test_default_values(self):
        user = UserInfo(user_id="u1")
        assert user.user_id == "u1"
        assert user.org_id is None
        assert user.roles == []
        assert user.permissions == []

    def test_full_values(self):
        user = UserInfo(
            user_id="u1",
            org_id="org-1",
            roles=["admin"],
            permissions=["agent:manage"],
        )
        assert user.org_id == "org-1"
        assert "admin" in user.roles
        assert "agent:manage" in user.permissions


class TestUserContext:
    def test_set_and_get(self):
        user = UserInfo(user_id="u1", org_id="org-1")
        set_current_user(user)
        retrieved = get_current_user()
        assert retrieved is not None
        assert retrieved.user_id == "u1"
        assert retrieved.org_id == "org-1"

    def test_default_is_none(self):
        set_current_user(None)
        assert get_current_user() is None

    def test_isolation_between_contextvars(self):
        """Verify that setting user in one context doesn't leak to another."""
        import contextvars
        ctx = contextvars.copy_context()

        user1 = UserInfo(user_id="u1")
        user2 = UserInfo(user_id="u2")

        results = []

        def run1():
            set_current_user(user1)
            results.append(("ctx1", get_current_user().user_id))
            return "done1"

        def run2():
            set_current_user(user2)
            results.append(("ctx2", get_current_user().user_id))
            return "done2"

        ctx.run(run1)
        ctx.run(run2)

        assert results == [("ctx1", "u1"), ("ctx2", "u2")]


class TestEnterpriseMiddleware:
    def test_cleans_up_context_after_request(self):
        app = FastAPI()
        app.add_middleware(EnterpriseMiddleware)

        @app.get("/test")
        def handler():
            set_current_user(UserInfo(user_id="test-user"))
            return {"ok": True}

        client = TestClient(app)
        client.get("/test")

        # After request completes, context should be cleaned up
        assert get_current_user() is None

    def test_cleans_up_even_on_error(self):
        app = FastAPI()
        app.add_middleware(EnterpriseMiddleware)

        @app.get("/error")
        def handler():
            set_current_user(UserInfo(user_id="test-user"))
            raise ValueError("boom")

        client = TestClient(app)
        try:
            client.get("/error")
        except Exception:
            pass

        # Even after error, context should be cleaned up
        assert get_current_user() is None
```

Run: `pytest tests/test_enterprise_context.py -v`
Expected: 6 tests FAIL (module not found)

- [ ] **Step 2: Implement context.py**

`src/qwenpaw/enterprise/context.py`:
```python
"""用户上下文 —— contextvars 实现的全链路用户身份传递。"""
import contextvars
from dataclasses import dataclass, field
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware


@dataclass
class UserInfo:
    """企业用户身份信息。"""
    user_id: str
    org_id: Optional[str] = None
    roles: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)


_current_user: contextvars.ContextVar[Optional[UserInfo]] = \
    contextvars.ContextVar("current_user", default=None)


def get_current_user() -> Optional[UserInfo]:
    return _current_user.get()


def set_current_user(user: Optional[UserInfo]) -> None:
    _current_user.set(user)


class EnterpriseMiddleware(BaseHTTPMiddleware):
    """请求结束后清理 UserContext，防止 contextvar 泄漏到下一个请求。

    注意：此中间件不做认证。用户身份的设置由 AuthMiddleware
    （HTTP API 路径）或通道消息处理（通道消息路径）完成。
    """

    async def dispatch(self, request, call_next):
        try:
            response = await call_next(request)
            return response
        finally:
            set_current_user(None)
```

Run: `pytest tests/test_enterprise_context.py -v`
Expected: 6 tests PASS

- [ ] **Step 3: Commit**

```bash
git add src/qwenpaw/enterprise/context.py tests/test_enterprise_context.py
git commit -m "feat(enterprise): add UserInfo, UserContext and EnterpriseMiddleware"
```

---

### Task 3: 可插拔认证提供者

**Files:**
- Create: `src/qwenpaw/enterprise/auth.py`
- Create: `tests/test_enterprise_auth.py`

- [ ] **Step 1: Write auth tests (failing)**

`tests/test_enterprise_auth.py`:
```python
from unittest.mock import AsyncMock, patch
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

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "user_id": "ent-user-1",
            "org_id": "org-1",
            "roles": ["employee"],
            "permissions": ["tool:read_file"],
        }

        mock_request = AsyncMock()
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

        mock_request = AsyncMock()
        mock_request.headers = {}

        result = await provider.authenticate(mock_request)
        assert result is None

    @pytest.mark.asyncio
    async def test_authenticate_service_error(self):
        provider = ExternalAuthProvider(service_url="http://auth:8080")

        mock_request = AsyncMock()
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

        mock_response = AsyncMock()
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

        mock_response = AsyncMock()
        mock_response.status_code = 404

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            result = await resolver.resolve("dingtalk", "unknown-user")

        assert result is None
```

Run: `pytest tests/test_enterprise_auth.py -v`
Expected: 6 tests FAIL (module not found)

- [ ] **Step 2: Implement auth.py**

`src/qwenpaw/enterprise/auth.py`:
```python
"""可插拔认证提供者协议 + 实现。"""
from typing import Optional, Protocol, runtime_checkable
import logging

import httpx

from .context import UserInfo

logger = logging.getLogger(__name__)


@runtime_checkable
class AuthProvider(Protocol):
    """HTTP API 路径的认证提供者。"""

    async def authenticate(self, request) -> Optional[UserInfo]:
        ...


@runtime_checkable
class UserResolver(Protocol):
    """通道消息路径的用户解析器。

    将通道用户 ID（如钉钉 userid、飞书 open_id）映射为企业用户。
    """

    async def resolve(self, channel: str, channel_user_id: str) -> Optional[UserInfo]:
        ...


class NoopAuthProvider:
    """默认实现：保持 QwenPaw 原有认证行为不变。"""

    async def authenticate(self, request) -> Optional[UserInfo]:
        return None


class ExternalAuthProvider:
    """HTTP 调用企业认证服务验证 Bearer token。"""

    def __init__(self, service_url: str, token_header: str = "X-Auth-Token",
                 timeout: float = 5.0):
        self.service_url = service_url.rstrip("/")
        self.token_header = token_header
        self.timeout = timeout

    async def authenticate(self, request) -> Optional[UserInfo]:
        token = request.headers.get(self.token_header.lower())
        if not token:
            return None
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.service_url}/api/auth/verify",
                    headers={self.token_header: token},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return UserInfo(
                        user_id=data["user_id"],
                        org_id=data.get("org_id"),
                        roles=data.get("roles", []),
                        permissions=data.get("permissions", []),
                    )
                return None
        except Exception:
            logger.warning("Auth service unreachable", exc_info=True)
            return None


class ExternalUserResolver:
    """HTTP 调用企业认证服务，将通道用户映射为企业用户。"""

    def __init__(self, service_url: str, timeout: float = 5.0):
        self.service_url = service_url.rstrip("/")
        self.timeout = timeout

    async def resolve(self, channel: str, channel_user_id: str) -> Optional[UserInfo]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.service_url}/api/auth/resolve",
                    json={"channel": channel, "channel_user_id": channel_user_id},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return UserInfo(
                        user_id=data["user_id"],
                        org_id=data.get("org_id"),
                        roles=data.get("roles", []),
                        permissions=data.get("permissions", []),
                    )
                return None
        except Exception:
            logger.warning("Auth resolve service unreachable", exc_info=True)
            return None
```

Run: `pytest tests/test_enterprise_auth.py -v`
Expected: 6 tests PASS

- [ ] **Step 3: Commit**

```bash
git add src/qwenpaw/enterprise/auth.py tests/test_enterprise_auth.py
git commit -m "feat(enterprise): add pluggable AuthProvider and UserResolver"
```

---

### Task 4: 可插拔权限提供者

**Files:**
- Create: `src/qwenpaw/enterprise/permissions.py`
- Create: `tests/test_enterprise_permissions.py`

- [ ] **Step 1: Write permissions tests (failing)**

`tests/test_enterprise_permissions.py`:
```python
from unittest.mock import AsyncMock, patch
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

        mock_response = AsyncMock()
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

        mock_response = AsyncMock()
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

        mock_response = AsyncMock()
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
```

Run: `pytest tests/test_enterprise_permissions.py -v`
Expected: 5 tests FAIL (module not found)

- [ ] **Step 2: Implement permissions.py**

`src/qwenpaw/enterprise/permissions.py`:
```python
"""可插拔权限决策提供者协议 + 实现。"""
from typing import Optional, Protocol, runtime_checkable
import logging

import httpx

from .context import UserInfo

logger = logging.getLogger(__name__)


@runtime_checkable
class PermissionProvider(Protocol):
    """权限决策协议。在工具执行、Agent 访问等场景调用。"""

    async def check_permission(
        self, user: UserInfo, action: str, resource: str,
    ) -> bool:
        """检查用户是否有权限执行某操作。"""
        ...


class NoopPermissionProvider:
    """默认实现：允许所有操作（保持现有行为不变）。"""

    async def check_permission(
        self, user: UserInfo, action: str, resource: str,
    ) -> bool:
        return True


class ExternalPermissionProvider:
    """HTTP 调用企业 RBAC 服务。超时或不可用时 fail-closed（拒绝）。"""

    def __init__(self, service_url: str, timeout: float = 3.0):
        self.service_url = service_url.rstrip("/")
        self.timeout = timeout

    async def check_permission(
        self, user: UserInfo, action: str, resource: str,
    ) -> bool:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.service_url}/api/rbac/check",
                    json={
                        "user_id": user.user_id,
                        "org_id": user.org_id,
                        "roles": user.roles,
                        "action": action,
                        "resource": resource,
                    },
                )
                return resp.json().get("allowed", False)
        except Exception:
            logger.warning(
                "RBAC service unreachable, denying permission for %s:%s",
                action,
                resource,
                exc_info=True,
            )
            return False
```

Run: `pytest tests/test_enterprise_permissions.py -v`
Expected: 5 tests PASS

- [ ] **Step 3: Commit**

```bash
git add src/qwenpaw/enterprise/permissions.py tests/test_enterprise_permissions.py
git commit -m "feat(enterprise): add pluggable PermissionProvider with fail-closed semantics"
```

---

### Task 5: AuthMiddleware — 用户上下文设置

**Files:**
- Modify: `src/qwenpaw/app/auth.py`

- [ ] **Step 1: Modify AuthMiddleware.dispatch() to set UserContext**

In `src/qwenpaw/app/auth.py`, in `AuthMiddleware.dispatch()`, after line `request.state.user = user` and before `return await call_next(request)`, add:

```python
        # Enterprise multi-user: set UserContext from external auth service.
        # When enterprise mode is not enabled, is_enterprise_mode() returns
        # False and this block is skipped entirely.
        from qwenpaw.enterprise.auth import get_auth_provider
        from qwenpaw.enterprise.context import set_current_user

        auth_provider = get_auth_provider()
        if auth_provider is not None:
            try:
                enterprise_user = await auth_provider.authenticate(request)
                if enterprise_user is not None:
                    set_current_user(enterprise_user)
                    request.state.user = enterprise_user.user_id
            except Exception:
                pass  # Fail open: fall back to QwenPaw's own auth

        return await call_next(request)
```

This replaces the existing `return await call_next(request)` line.

- [ ] **Step 2: Verify existing auth tests still pass**

Run: `pytest tests/ -k "auth" -v --timeout=30 2>/dev/null || echo "No auth-specific tests, checking imports"`

Run this Python check:
```bash
python -c "from qwenpaw.app.auth import AuthMiddleware; print('import OK')"
```
Expected: `import OK`

- [ ] **Step 3: Write integration test for UserContext flow**

Create `tests/test_enterprise_auth_middleware.py`:
```python
"""Integration test: AuthMiddleware + UserContext flow."""
from unittest.mock import AsyncMock, patch
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

        from qwenpaw.enterprise.auth import get_auth_provider
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

    mock_response = AsyncMock()
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
```

Run: `pytest tests/test_enterprise_auth_middleware.py -v`
Expected: 2 tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/qwenpaw/app/auth.py tests/test_enterprise_auth_middleware.py
git commit -m "feat(enterprise): integrate UserContext into AuthMiddleware dispatch"
```

---

### Task 6: FastAPI 应用集成

**Files:**
- Modify: `src/qwenpaw/app/_app.py`

- [ ] **Step 1: Register EnterpriseMiddleware and add lifecycle hooks**

In `src/qwenpaw/app/_app.py`:

**A.** After `app.add_middleware(AuthMiddleware)` (approximately line 565), add:

```python
# Enterprise middleware — cleans up UserContext after each request.
# Must be registered AFTER AuthMiddleware so the context still exists
# when the request is processed.
from qwenpaw.enterprise.context import EnterpriseMiddleware
app.add_middleware(EnterpriseMiddleware)
```

**B.** In `_background_startup()`, after `config = load_config()` (approximately line 337), add:

```python
            # ---- Enterprise features init ----
            if config.enterprise.enabled and config.enterprise.auth.mode == "external":
                from qwenpaw.enterprise.auth import ExternalAuthProvider, ExternalUserResolver
                from qwenpaw.enterprise.permissions import ExternalPermissionProvider
                from qwenpaw.enterprise import set_providers

                auth_url = config.enterprise.auth.service_url
                rbac_url = config.enterprise.rbac.service_url

                set_providers(
                    ExternalAuthProvider(service_url=auth_url),
                    ExternalUserResolver(service_url=auth_url),
                    ExternalPermissionProvider(service_url=rbac_url),
                )
                logger.info("Enterprise mode enabled (auth=%s, rbac=%s)", auth_url, rbac_url)
```

**C.** In the shutdown section (inside `finally` after `yield`, before `plugin_registry` check), add:

```python
        # ---- Enterprise features teardown ----
        from qwenpaw.enterprise import clear_providers
        clear_providers()
```

- [ ] **Step 2: Verify app still starts**

```bash
python -c "from qwenpaw.app._app import app; print('app created OK, routes:', len(app.routes))"
```
Expected: `app created OK, routes: <number>`

- [ ] **Step 3: Commit**

```bash
git add src/qwenpaw/app/_app.py
git commit -m "feat(enterprise): register EnterpriseMiddleware and lifecycle hooks in FastAPI app"
```

---

### Task 7: 工具执行权限检查

**Files:**
- Modify: `src/qwenpaw/agents/tool_guard_mixin.py`

- [ ] **Step 1: Add permission check helper method**

In `src/qwenpaw/agents/tool_guard_mixin.py`, add to the `ToolGuardMixin` class:

```python
    async def _check_enterprise_permission(
        self, tool_name: str, tool_input: dict,
    ) -> bool:
        """查询企业权限服务，判断当前用户是否可以执行此工具。

        Returns True if:
        - Enterprise mode is not enabled (no current user)
        - Permission provider is not configured
        - Permission service returns allowed=True
        """
        from qwenpaw.enterprise.context import get_current_user
        from qwenpaw.enterprise.permissions import get_permission_provider

        user = get_current_user()
        if user is None:
            return True  # Not enterprise mode — allow

        provider = get_permission_provider()
        if provider is None:
            return True  # No permission service configured — allow

        return await provider.check_permission(
            user=user,
            action=f"tool:{tool_name}",
            resource=tool_name,
        )
```

- [ ] **Step 2: Inject permission check in _decide_guard_action (position A)**

In `_decide_guard_action`, find the line `if guard_result is None or not guard_result.findings:\n            return None` (approximately line 265) and replace that `return None` with:

```python
            if guard_result is None or not guard_result.findings:
                if not await self._check_enterprise_permission(
                    tool_name, tool_input,
                ):
                    return _GuardAction(
                        "auto_denied", tool_name, tool_input,
                        guard_result=self._create_permission_denied_result(tool_name),
                    )
                return None
```

- [ ] **Step 3: Inject permission check in _acting_with_approval (position B)**

In `_acting_with_approval`, after `if decision == ApprovalDecision.APPROVED:` and before `return await super()._acting(tool_call)` (approximately line 494), add:

```python
        if decision == ApprovalDecision.APPROVED:
            if not await self._check_enterprise_permission(tool_name, tool_input):
                return await self._acting_denied(
                    tool_call, tool_name, guard_result,
                )
            return await super()._acting(tool_call)
```

- [ ] **Step 4: Add helper to create permission-denied guard result**

Add to `ToolGuardMixin` class:

```python
    def _create_permission_denied_result(self, tool_name: str):
        """Create a guard result for enterprise permission denial."""
        from qwenpaw.security.tool_guard.models import (
            GuardSeverity,
            GuardThreatCategory,
            GuardFinding,
            ToolGuardResult,
        )
        import uuid as _uuid

        finding = GuardFinding(
            id=str(_uuid.uuid4())[:8],
            rule_id="enterprise_rbac",
            category=GuardThreatCategory.RESOURCE_ABUSE,
            severity=GuardSeverity.HIGH,
            title="Enterprise Permission Denied",
            description=(
                f"User does not have enterprise permission to use tool '{tool_name}'"
            ),
            tool_name=tool_name,
            param_name=None,
            matched_value=None,
            matched_pattern=None,
            snippet=None,
            remediation="Contact your administrator to request access",
            guardian="enterprise_rbac",
            metadata={"reason": "enterprise_permission_denied"},
        )

        return ToolGuardResult(
            tool_name=tool_name,
            params={},
            findings=[finding],
            guardians_used=["enterprise_rbac"],
        )
```

- [ ] **Step 5: Verify import chain**

```bash
python -c "from qwenpaw.agents.tool_guard_mixin import ToolGuardMixin; print('import OK')"
```
Expected: `import OK`

- [ ] **Step 6: Commit**

```bash
git add src/qwenpaw/agents/tool_guard_mixin.py
git commit -m "feat(enterprise): inject enterprise permission check in tool guard"
```

---

### Task 8: Agent 管理 API 权限检查

**Files:**
- Modify: `src/qwenpaw/app/routers/agents.py`

- [ ] **Step 1: Add permission check helper**

At the top of `src/qwenpaw/app/routers/agents.py`, after existing imports, add:

```python
from qwenpaw.enterprise.context import get_current_user
from qwenpaw.enterprise.permissions import get_permission_provider


async def _check_agent_permission(action: str, agent_id: str) -> bool:
    """Check enterprise permission for agent management operations.

    Returns True if enterprise mode is not enabled, or if the
    current user has the required permission.
    """
    user = get_current_user()
    if user is None:
        return True  # Not enterprise mode

    provider = get_permission_provider()
    if provider is None:
        return True  # No permission service configured

    return await provider.check_permission(
        user=user,
        action=action,
        resource=agent_id,
    )
```

- [ ] **Step 2: Add permission check in create_agent endpoint**

In the `create_agent` handler function, add after the function body begins:

```python
    if not await _check_agent_permission("agent:create", "*"):
        raise HTTPException(status_code=403, detail="Permission denied")
```

- [ ] **Step 3: Add permission check in delete_agent endpoint**

In the `delete_agent` handler function, add:

```python
    if not await _check_agent_permission("agent:delete", agent_id):
        raise HTTPException(status_code=403, detail="Permission denied")
```

- [ ] **Step 4: Add permission check in update_agent_config endpoint**

In the `update_agent_config` (or equivalent PUT/PATCH) handler function, add:

```python
    if not await _check_agent_permission("agent:update", agent_id):
        raise HTTPException(status_code=403, detail="Permission denied")
```

- [ ] **Step 5: Verify imports**

```bash
python -c "from qwenpaw.app.routers.agents import router; print('import OK, routes:', len(router.routes))"
```
Expected: `import OK, routes: <number>`

- [ ] **Step 6: Commit**

```bash
git add src/qwenpaw/app/routers/agents.py
git commit -m "feat(enterprise): add permission checks to agent management API"
```

---

### Task 9: 通道消息 — 用户身份注入点

**Files:**
- Modify: 各通道的消息处理入口（具体文件路径见下方说明）

> 注：此任务为通用模式指导。具体注入点取决于各通道架构。QwenPaw 的通道模块可能随上游迭代变化，实现时需确认当前的消息处理入口。

- [ ] **Step 1: 通用注入模式**

在各通道的消息入口处（消息从 webhook 进入、调用 Runner 之前），注入以下逻辑：

```python
# 在消息进入 Runner 之前插入：
from qwenpaw.enterprise.context import set_current_user
from qwenpaw.enterprise.auth import get_user_resolver

resolver = get_user_resolver()
if resolver is not None:
    user_info = await resolver.resolve(
        channel="dingtalk",           # 通道标识: "dingtalk"|"feishu"|"wechat"|"wecom"
        channel_user_id=sender_id,    # 通道原始用户 ID
    )
    if user_info is not None:
        set_current_user(user_info)
```

**通道标识常量对应关系：**
- 钉钉 → `"dingtalk"`
- 飞书 → `"feishu"`
- 企微 → `"wecom"`
- 微信 → `"wechat"`
- Telegram → `"telegram"`
- Discord → `"discord"`

- [ ] **Step 2: DingTalk 通道示例**

以钉钉为例（参考 `src/qwenpaw/channels/dingtalk/` 目录的消息处理函数），在收到消息、提取 sender_id 之后、调用 runner 之前插入上述代码。`channel` 固定为 `"dingtalk"`，`channel_user_id` 为钉钉消息中的 `senderStaffId` 或 `senderId`。

- [ ] **Step 3: 验证（不需要额外测试，通道层已有集成测试覆盖）**

```bash
python -c "from qwenpaw.enterprise.auth import ExternalUserResolver; r = ExternalUserResolver('http://localhost:8080'); print('resolver OK')"
```
Expected: `resolver OK`

- [ ] **Step 4: Commit**

```bash
git add src/qwenpaw/channels/  # or specific channel files
git commit -m "feat(enterprise): add channel user identity resolution hooks"
```

---

### Task 10: 全量集成验证

**Files:**
- Create: `tests/test_enterprise_integration.py`

- [ ] **Step 1: 端到端集成测试**

`tests/test_enterprise_integration.py`:
```python
"""Enterprise multi-user integration tests — end-to-end."""
from unittest.mock import AsyncMock, patch
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
        from qwenpaw.enterprise.auth import get_auth_provider

        mock_request = AsyncMock()
        mock_request.headers = {"x-auth-token": "token-xyz"}

        auth_response = AsyncMock()
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
        from qwenpaw.enterprise.permissions import get_permission_provider

        perm_response_allowed = AsyncMock()
        perm_response_allowed.json.return_value = {"allowed": True}

        perm_response_denied = AsyncMock()
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
        """Simulate DingTalk channel message → user resolution → permission check."""
        # 1. Set up providers with UserResolver
        set_providers(
            None,  # No HTTP auth provider (channel messages don't use Bearer tokens)
            ExternalUserResolver(service_url="http://auth:8080"),
            ExternalPermissionProvider(service_url="http://rbac:8080"),
        )

        # 2. Simulate channel receiving a message from DingTalk user
        from qwenpaw.enterprise.auth import get_user_resolver

        resolve_response = AsyncMock()
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
        from qwenpaw.enterprise.permissions import get_permission_provider

        perm_response = AsyncMock()
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
```

Run: `pytest tests/test_enterprise_integration.py -v`
Expected: 3 tests PASS

- [ ] **Step 2: Run all enterprise tests**

```bash
pytest tests/test_enterprise_*.py -v
```
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_enterprise_integration.py
git commit -m "test(enterprise): add end-to-end integration tests"
```

---

## 总结

| Task | 内容 | 新建文件 | 修改文件 |
|------|------|---------|---------|
| 1 | EnterpriseConfig 模型 | 3 | 1 |
| 2 | UserContext + EnterpriseMiddleware | 2 | 0 |
| 3 | AuthProvider + UserResolver | 2 | 0 |
| 4 | PermissionProvider | 2 | 0 |
| 5 | AuthMiddleware 集成 | 1 (test) | 1 |
| 6 | FastAPI 应用集成 | 0 | 1 |
| 7 | 工具执行权限检查 | 0 | 1 |
| 8 | Agent 管理 API 鉴权 | 0 | 1 |
| 9 | 通道消息用户解析 | 0 | 1-N (按需) |
| 10 | 集成验证 | 1 | 0 |

**预计总改动量：** 新增 ~350 行，修改 ~100 行
