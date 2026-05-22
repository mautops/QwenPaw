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
