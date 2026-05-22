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


from .context import get_current_user, set_current_user
from .auth import NoopAuthProvider
from .permissions import NoopPermissionProvider
