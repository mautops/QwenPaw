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
