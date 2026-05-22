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
