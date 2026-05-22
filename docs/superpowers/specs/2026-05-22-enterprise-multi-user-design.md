# QwenPaw 企业级多用户改造设计

## 概述

将 QwenPaw 从"单用户个人助手"改造为"企业级多用户 Agent 运行时"，支持：
- **混合模式组织架构**：集团级共享 Agent + 部门级专属 Agent，形成层级体系
- **RBAC 权限管控**：对接外部权限服务，控制 Agent 使用和管理权限
- **组织与用户管理**：作为独立微服务，与 QwenPaw 通过 HTTP 接口通信

企业功能（组织管理、RBAC、审计、管理控制台）作为**独立微服务**开发部署，QwenPaw 本体做最小改造，保持与上游的合并兼容性。

## 核心原则

1. **最小侵入**：QwenPaw 改造量控制在 ~350 行，集中在 6 个现有文件 + 1 个新目录（5 个文件）
2. **默认零影响**：`enterprise.enabled = false` 时完全等同于原生 QwenPaw
3. **扩展点模式**：通过 Protocol 定义可插拔接口，默认实现保持现有行为
4. **合并友好**：所有改动都是"增量"的，不修改现有核心逻辑流程

## 代码管理策略

- 基于 fork + 定期从 upstream 拉取合并
- 不改动上游，所有改动保持在 fork 内部
- 新增文件放在 `src/qwenpaw/enterprise/` 独立目录，上游不存在此目录 → 零冲突
- 现有文件改动遵循"只在末尾追加、只在安全位置插入"原则

---

## 架构总览

```
                        ┌─────────────────────────────────┐
                        │      企业认证服务 (独立微服务)      │
                        │   SSO / Token验证 / 用户解析      │
                        │   通道用户→企业用户 身份映射        │
                        └──────────┬──────────┬───────────┘
                                   │ HTTP     │ HTTP
                        ┌──────────┴──────────┴───────────┐
                        │      企业权限服务 (独立微服务)      │
                        │   RBAC / 角色 / 权限决策          │
                        └──────────────┬──────────────────┘
                                       │ HTTP
┌──────────────────────────────────────┴──────────────────────────────────────┐
│                           QwenPaw 本体 (最小改造)                             │
│                                                                              │
│  请求处理链:                                                                  │
│  ┌──────────────┐   ┌──────────────────┐   ┌────────────────────────────┐   │
│  │AuthMiddleware │──→│ AuthMiddleware   │──→│ EnterpriseMiddleware        │   │
│  │(现有,不改动)  │   │[新增] 设置        │   │[新增] 请求结束后清理         │   │
│  │JWT验证+放行   │   │UserContext       │   │UserContext                 │   │
│  └──────────────┘   └──────────────────┘   └────────────────────────────┘   │
│                                 │                                            │
│  通道消息路径 (非 HTTP API):                                                  │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  ChannelManager.receive() → [新增] channel_user_id → enterprise_user │   │
│  │  通过 UserResolver 映射 → 设置 UserContext → Runner 处理              │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                 │                                            │
│  ┌──────────────────────────────┴──────────────────────────────────────┐    │
│  │  新增: src/qwenpaw/enterprise/                                       │    │
│  │  ├── __init__.py                                                      │    │
│  │  ├── context.py        UserInfo + UserContext + EnterpriseMiddleware │    │
│  │  ├── auth.py           AuthProvider 协议 +UserResolver协议 + 工厂    │    │
│  │  ├── permissions.py    PermissionProvider 协议 + 工厂                │    │
│  │  └── config.py         EnterpriseConfig 配置模型                     │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌──────────────────────────────┬──────────────────────────────────────┐    │
│  │  改造现有文件                                                        │    │
│  │  ├── config/config.py        +EnterpriseConfig 字段                  │    │
│  │  ├── app/auth.py             +用户上下文设置                         │    │
│  │  ├── app/_app.py             +中间件注册 + 组件初始化 + 关闭          │    │
│  │  ├── app/routers/agents.py   +Agent 管理权限检查                     │    │
│  │  ├── agents/tool_guard_mixin.py +工具执行权限检查                    │    │
│  │  └── app/runner/runner.py    +消息入口用户解析                       │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 两条认证路径

| 路径 | 触发场景 | 用户身份来源 |
|------|---------|-------------|
| **HTTP API 路径** | Console 前端、外部 API 调用 | Bearer token → AuthMiddleware 验证 → AuthProvider 解析 |
| **通道消息路径** | 钉钉/飞书/企微用户发消息 | Channel webhook → channel_user_id → UserResolver 映射为企业用户 |

---

## 新增模块

### `src/qwenpaw/enterprise/__init__.py`

模块入口，提供 Provider 注册/获取/清除函数（模块级单例）。

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

### `src/qwenpaw/enterprise/context.py`

用户上下文 —— 基于 `contextvars` 的全链路用户身份传递。

- `UserInfo`：企业用户身份数据类
- `get_current_user()` / `set_current_user()`：设置和读取当前请求用户
- `EnterpriseMiddleware`：FastAPI 中间件，**仅负责请求结束后清理 contextvar**，不做认证。用户身份的设置由 AuthMiddleware（HTTP API 路径）或 ChannelManager（通道消息路径）完成

```python
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
    注意：此中间件不做认证，认证逻辑在 AuthMiddleware 中。
    """
    async def dispatch(self, request, call_next):
        try:
            response = await call_next(request)
            return response
        finally:
            set_current_user(None)
```

### `src/qwenpaw/enterprise/auth.py`

可插拔认证提供者协议 + 工厂函数。两个协议应对两种认证路径：

| 协议 | 场景 | 输入 | 输出 |
|------|------|------|------|
| `AuthProvider` | HTTP API 路径 | HTTP Request (Bearer token) | `UserInfo \| None` |
| `UserResolver` | 通道消息路径 | channel + channel_user_id | `UserInfo \| None` |

- `LocalAuthProvider`：默认实现，保持 QwenPaw 原有认证不变
- `ExternalAuthProvider`：HTTP 调用企业认证服务验证 Bearer token
- `ExternalUserResolver`：HTTP 调用企业认证服务，将通道用户映射为企业用户
- `get_auth_provider()` / `get_user_resolver()`：根据配置返回对应实现
- `is_enterprise_mode()`：检查 `enterprise.enabled`

```python
from typing import Optional, Protocol, runtime_checkable
from .context import UserInfo

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

class ExternalUserResolver:
    """调用企业认证服务完成通道用户 → 企业用户映射。"""
    def __init__(self, service_url: str):
        self.service_url = service_url

    async def resolve(self, channel: str, channel_user_id: str) -> Optional[UserInfo]:
        # POST {service_url}/api/auth/resolve
        # Body: { channel, channel_user_id }
        # Response: { user_id, org_id, roles: [], permissions: [] }
        ...
```

### `src/qwenpaw/enterprise/permissions.py`

可插拔权限决策提供者协议 + 工厂函数。

**降级策略**：权限服务不可用时 **fail-closed**（拒绝执行），避免绕过权限管控。

- `NoopPermissionProvider`：默认实现，允许所有操作（保持现有行为）
- `ExternalPermissionProvider`：HTTP 调用企业 RBAC 服务，默认超时 3 秒，超时或网络错误时返回 `False`（拒绝）
- `get_permission_provider()`：根据配置返回对应实现

```python
class ExternalPermissionProvider:
    def __init__(self, service_url: str, timeout: float = 3.0):
        self.service_url = service_url
        self.timeout = timeout

    async def check_permission(
        self, user: UserInfo, action: str, resource: str
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
            # fail-closed: 权限服务不可用时拒绝执行
            return False
```

**性能考量**：每个工具调用都会触发一次权限检查 HTTP 请求。QwenPaw 的 ReActAgent 在一次对话中可能调用多个工具，需要考虑延迟叠加。优化策略（后续迭代）：
1. 请求级缓存：同一请求内相同 (user, action, resource) 的结果缓存
2. 批量预检：Agent 初始化时预检常用工具权限
3. 短期 Token：由认证服务签发含权限声明的短期 token，避免每次查询

### `src/qwenpaw/enterprise/config.py`

企业功能配置模型。

```python
from pydantic import BaseModel, Field

class EnterpriseAuthConfig(BaseModel):
    mode: str = "local"      # "local" | "external"
    service_url: str = ""
    token_header: str = "X-Auth-Token"

class EnterpriseRbacConfig(BaseModel):
    service_url: str = ""

class EnterpriseConfig(BaseModel):
    enabled: bool = False
    auth: EnterpriseAuthConfig = Field(default_factory=EnterpriseAuthConfig)
    rbac: EnterpriseRbacConfig = Field(default_factory=EnterpriseRbacConfig)
```

---

## 现有文件改造

### 1. `src/qwenpaw/config/config.py`

**改动**：在 `Config` 模型末尾新增一个字段（~3 行）

```python
# 约第 1754 行 user_timezone 之后
enterprise: EnterpriseConfig = Field(default_factory=EnterpriseConfig)
```

**冲突风险**：极低。只在 Pydantic 模型末尾追加一个 optional 字段。

### 2. `src/qwenpaw/app/auth.py`

**改动**：在 `AuthMiddleware.dispatch()` 方法中，token 验证通过后加入用户上下文设置（~25 行新增代码，加在 `request.state.user = user` 之后、`return await call_next(request)` 之前）

```
现有流程:
  1. 跳过公开路径 (不变)
  2. 提取 Bearer token (不变)
  3. 验证 token → 失败返回 401 (不变)
  4. 设置 request.state.user = user (不变)
  5. [新增] 如果企业模式启用，调用 AuthProvider 获取完整用户身份
     - 调用外部认证服务验证 token
     - 获取 user_id, org_id, roles
     - 设置 UserContext (contextvars)
     - 用 enterprise user_id 覆盖 request.state.user
  6. 放行到下一层 (不变)
```

**冲突风险**：低。只在 `dispatch()` 末尾插入不修改已有逻辑。如果上游重构了 AuthMiddleware 整体架构才需要适配。

### 3. `src/qwenpaw/app/_app.py`

**改动**：注册中间件 + 初始化/销毁企业组件（~25 行）

```python
# A. 中间件注册: 在 app = FastAPI(...) 之后、AuthMiddleware 之后
from qwenpaw.enterprise.context import EnterpriseMiddleware
app.add_middleware(EnterpriseMiddleware)

# B. 企业组件初始化: 在 _background_startup() 中，load_config 之后
if config.enterprise.enabled:
    from qwenpaw.enterprise.auth import ExternalAuthProvider, ExternalUserResolver
    from qwenpaw.enterprise.permissions import ExternalPermissionProvider
    from qwenpaw.enterprise import set_providers

    auth_provider = ExternalAuthProvider(
        service_url=config.enterprise.auth.service_url,
    )
    user_resolver = ExternalUserResolver(
        service_url=config.enterprise.auth.service_url,
    )
    perm_provider = ExternalPermissionProvider(
        service_url=config.enterprise.rbac.service_url,
    )
    set_providers(auth_provider, user_resolver, perm_provider)

# C. 组件销毁: 在 lifespan 的 finally → shutdown 中
from qwenpaw.enterprise import clear_providers
clear_providers()
```

**冲突风险**：极低。中间件注册是独立行；初始化和销毁逻辑在现有的 `_background_startup()` 和 shutdown 流程中追加。

### 4. `src/qwenpaw/agents/tool_guard_mixin.py`

**改动**：在两处"放行"路径前加入权限检查（~30 行）

- 位置 A（`_decide_guard_action` 中 guard 检查通过时，约第 265 行）：`return None` 前调用 `_check_enterprise_permission()`
- 位置 B（`_acting_with_approval` 中用户批准后，约第 494 行）：`super()._acting(tool_call)` 前调用权限检查

新增辅助方法 `_check_enterprise_permission()`，逻辑：
```
1. 获取当前用户 → 如果为 None（非企业模式），返回 True（放行）
2. 获取权限提供者 → 如果为 None（未配置），返回 True（放行）
3. 调用 provider.check_permission(user, "tool:<name>", <name>)
4. 返回权限决策结果
```

**冲突风险**：中等。如果上游对 `_acting` 或 `_decide_guard_action` 做了大重构，需要相应适配插入位置。但由于改动是"在 return 前加一行方法调用"，适配成本低。

### 5. 通道消息入口 — 用户身份注入（~20 行）

**场景**：用户通过钉钉/飞书/企微等通道发消息，请求不经过 HTTP API 的 Bearer token 认证。

**改动位置**：在 `ChannelManager` 处理入站消息、调用 Runner 之前，增加企业用户身份解析。

具体注入点取决于各通道的消息处理流程，通用模式为：

```python
# 在消息进入 Runner 之前:
from qwenpaw.enterprise.context import set_current_user
from qwenpaw.enterprise.auth import get_user_resolver

resolver = get_user_resolver()
if resolver:
    user_info = await resolver.resolve(
        channel="dingtalk",            # 通道标识
        channel_user_id=sender_id,     # 通道用户 ID
    )
    if user_info:
        set_current_user(user_info)
```

**冲突风险**：低。在消息处理的早期阶段插入，不修改通道核心逻辑。

> 注：具体文件路径取决于各通道的消息处理入口（如 `channels/dingtalk/`、`channels/feishu/` 等）。由于 QwenPaw 的通道模块结构可能在迭代中变化，此处仅描述通用模式。实现时可按通道逐一添加，或抽象为 ChannelManager 层的统一钩子。

---

### 6. `src/qwenpaw/app/routers/agents.py`

**改动**：在 Agent 管理 API 端点（创建/删除/修改）增加权限检查（~15 行）

涉及的端点：
- `POST /api/agents` — 创建 Agent
- `DELETE /api/agents/{agent_id}` — 删除 Agent
- `PUT /api/agents/{agent_id}/config` — 修改 Agent 配置

每个端点在函数体开头新增：
```python
if not await _check_agent_permission(request, agent_id):
    raise HTTPException(status_code=403, detail="Permission denied")
```

**冲突风险**：低。在函数体开头插入检查，如果上游修改了函数签名才需要适配。

---

## 影响汇总

| 文件 | 改动类型 | 净增行 | 冲突风险 |
|------|----------|--------|----------|
| `qwenpaw/enterprise/__init__.py` | **新增** | ~35 | 零 |
| `qwenpaw/enterprise/context.py` | **新增** | ~45 | 零 |
| `qwenpaw/enterprise/auth.py` | **新增** | ~70 | 零 |
| `qwenpaw/enterprise/permissions.py` | **新增** | ~55 | 零 |
| `qwenpaw/enterprise/config.py` | **新增** | ~20 | 零 |
| `config/config.py` | 新增 1 个字段 | +3 | 极低 |
| `app/auth.py` | `dispatch()` 末尾追加 | +25 | 低 |
| `app/_app.py` | 中间件注册 + 初始化 + 销毁 | +25 | 极低 |
| `agents/tool_guard_mixin.py` | 两处 return 前插入检查 | +30 | 中等 |
| `app/routers/agents.py` | 3 个端点函数体前插入检查 | +15 | 低 |
| 通道消息入口 | 1-3 个通道文件，各 ~5 行 | +20 | 低 |
| **总计** | | **~343** | |

---

## 运行时行为

### enterprise.enabled = false (默认)
- `get_auth_provider()` 返回 None
- `get_permission_provider()` 返回 None
- 所有新增代码路径在第一步就返回（`get_current_user()` → None → 放行）
- **完全等同于原生 QwenPaw**

### enterprise.enabled = true, auth.mode = "external"
- 启动时根据配置创建 `ExternalAuthProvider` 和 `ExternalPermissionProvider`
- 每个请求：AuthMiddleware → 调用外部认证服务 → 设置 UserContext
- 工具执行前：ToolGuard → 权限检查 → 调用外部 RBAC 服务
- Agent 管理 API：权限检查 → 调用外部 RBAC 服务

### 企业微服务 API 约定

**认证服务** 需实现两个端点：

```
POST /api/auth/verify
Header: X-Auth-Token: <token>
Body: (empty)
Response 200: { user_id, org_id, roles: [], permissions: [] }
Response 401: { detail: "invalid token" }

POST /api/auth/resolve
Body: { channel: "dingtalk", channel_user_id: "xxx" }
Response 200: { user_id, org_id, roles: [], permissions: [] }
Response 404: { detail: "unknown user" }
```

**权限服务** 需实现：

```
POST /api/rbac/check
Body: { user_id, org_id, roles: [], action: "tool:execute_shell_command", resource: "execute_shell_command" }
Response: { allowed: true/false }
Timeout: QwenPaw 侧默认 3 秒，超时视为拒绝
```

### 运行时行为补充

**enterprise.enabled = true 时，两种请求的完整处理链：**

| 步骤 | HTTP API 路径（前端/API） | 通道消息路径（钉钉/飞书/企微） |
|------|--------------------------|-------------------------------|
| 1 | AuthMiddleware 验证 JWT | Channel webhook 接收消息 |
| 2 | AuthProvider.authenticate() | UserResolver.resolve(channel, sender_id) |
| 3 | set_current_user(user_info) | set_current_user(user_info) |
| 4 | 业务逻辑（API handler） | Runner 处理消息 |
| 5 | 工具执行前 PermissionProvider.check() | 工具执行前 PermissionProvider.check() |
| 6 | EnterpriseMiddleware 清理 context | EnterpriseMiddleware 清理 context |

---

## 后续扩展点（不在本次范围）

1. **会话隔离增强**：当前 chat session 按 `channel_user_id` 隔离，企业模式下可能需要按 `enterprise_user_id` 重新组织
2. **Agent ↔ Org 绑定**：在 `AgentProfileConfig` 中增加 `org_id` 和 `visibility` 字段，实现 Agent 的部门归属和可见性控制
3. **资源配额**：在 LLM 调用前查询配额服务，按用户/部门限制 Token 使用量
4. **审计日志**：添加 `AuditEmitter` 协议，在关键操作（工具执行、Agent 访问）时发出事件

这些可以在当前基础上渐进式添加，不影响已有架构。
