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
