# User Context Passthrough — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thread user identity (`user_id`, `user_name`, `channel`, and custom metadata) from `AgentRequest.metadata` through the runtime to Tools, MCP Drivers, and Skill CLI subprocesses — via `request_context` and environment variables — without the LLM ever seeing it.

**Architecture:** Three targeted changes on the existing `request_context` pipeline: (1) restructure `_build_request_context()` to merge metadata before setting security-critical fields, (2) add a `current_request_context` ContextVar for tool functions to read, (3) inject `request_context` entries as `QWENPAW_*` environment variables in the shell tool, with output sanitization to prevent LLM information leakage.

**Tech Stack:** Python 3.11+, asyncio ContextVar, no new dependencies.

## Global Constraints

- Security-critical fields (`session_id`, `agent_id`, `root_session_id`, `root_agent_id`) MUST be set AFTER metadata merge — caller metadata cannot overwrite them.
- `_`-prefixed keys in metadata MUST be blocked (reserved for internal use).
- Metadata JSON serialization MUST NOT exceed 64 KiB.
- Environment variable keys MUST match `^[A-Za-z_][A-Za-z0-9_]*$` — invalid keys silently skipped.
- Shell output MUST be sanitized: any occurrence of a `QWENPAW_*` environment variable value in stdout/stderr replaced with `***REDACTED***`.
- No LLM visibility of user context — metadata is programmatic-only.

---

### Task 1: Add `current_request_context` ContextVar

**Files:**
- Modify: `src/qwenpaw/config/context.py` (append new section at end of file)

**Interfaces:**
- Produces:
  - `current_request_context: ContextVar[dict[str, Any] | None]`
  - `get_current_request_context() -> dict[str, Any] | None`
  - `set_current_request_context(ctx: dict[str, Any] | None) -> None`

- [ ] **Step 1: Add ContextVar, getter, and setter at end of file**

Append the following after the last function (`set_current_agent_state`) at line 192 of `src/qwenpaw/config/context.py`:

```python
# Context variable for per-request passthrough context.
# Set by AgentBuilder._build_request_context() during the agent-build phase
# so that tool functions (shell, file_io, browser, etc.) can read caller
# identity and custom metadata without depending on the LLM-visible prompt.
current_request_context: ContextVar[dict[str, Any] | None] = ContextVar(
    "current_request_context",
    default=None,
)


def get_current_request_context() -> dict[str, Any] | None:
    """Get the current request context dict, or None if not set.

    Returns:
        The per-request context dict containing user_id, channel,
        session_id, and any custom metadata fields, or None.
    """
    return current_request_context.get()


def set_current_request_context(ctx: dict[str, Any] | None) -> None:
    """Set the current request context dict.

    Args:
        ctx: Per-request context dict.  Set to None to clear.
    """
    current_request_context.set(ctx)
```

- [ ] **Step 2: Verify module still imports cleanly**

Run:
```bash
cd /Users/zhangsan/github/QwenPaw && python -c "from qwenpaw.config.context import get_current_request_context, set_current_request_context; print('OK')"
```

Expected: `OK` printed, no ImportError.

- [ ] **Step 3: Commit**

```bash
git add src/qwenpaw/config/context.py
git commit -m "feat(context): add current_request_context ContextVar for request passthrough

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: Restructure `_build_request_context()` — metadata-first merge

**Files:**
- Modify: `src/qwenpaw/runtime/builder.py:496-519` (`_build_request_context` method)

**Interfaces:**
- Consumes: `set_current_request_context` from `qwenpaw.config.context` (Task 1)
- Produces: `rc` dict with metadata merged before security-critical field assignment

- [ ] **Step 1: Add import at top of builder.py**

At line 18 (after the existing `from ..agents.acp.meta import ACP_CODING_PROJECT_META_KEY`), add:

```python
from ..config.context import set_current_request_context
```

- [ ] **Step 2: Add metadata size validation helper**

Add as a module-level function (after the `_logger` definition near line 15, before the `AgentBuilder` class):

```python
import json as _json

_MAX_METADATA_JSON_BYTES = 64 * 1024  # 64 KiB


def _validate_metadata_size(metadata: dict[str, Any]) -> None:
    """Reject metadata dicts whose JSON exceeds a size limit."""
    try:
        size = len(_json.dumps(metadata, default=str, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"AgentRequest.metadata is not JSON-serializable: {exc}",
        ) from exc
    if size > _MAX_METADATA_JSON_BYTES:
        raise ValueError(
            f"AgentRequest.metadata too large: {size} bytes "
            f"(max {_MAX_METADATA_JSON_BYTES})",
        )
```

- [ ] **Step 3: Rewrite `_build_request_context()` with metadata-first merge**

Replace the existing `_build_request_context` method (lines 496–519) with the following:

```python
@staticmethod
def _build_request_context(ctx: Any) -> dict[str, Any]:
    """Build per-request context dict with metadata-first merge.

    Caller-supplied metadata (AgentRequest.metadata) is merged into the
    context FIRST so that user-owned fields like user_id, user_name, and
    custom keys are available downstream.  Security-critical fields
    (session_id, agent_id, root_session_id, root_agent_id) are assigned
    AFTER the merge and cannot be overwritten by metadata.
    """
    request = getattr(ctx, "request", None)

    rc: dict[str, Any] = {}

    # ── Phase 1: user-owned metadata (merged first) ──
    _request_metadata = getattr(request, "metadata", None) if request else None
    if isinstance(_request_metadata, dict):
        _validate_metadata_size(_request_metadata)
        for key, value in _request_metadata.items():
            if not key.startswith("_"):      # block _-prefixed internal keys
                rc[key] = value

    # ── Phase 2: channel metadata ──
    _channel_meta = (
        getattr(request, "channel_meta", None) if request else None
    )
    if isinstance(_channel_meta, dict):
        user_name = _channel_meta.get("user_name")
        if user_name:
            rc.setdefault("user_name", user_name)
        rc.setdefault("channel_meta", _channel_meta)

    # ── Phase 3: request_context payload (approval_level overrides etc.) ──
    _payload_ctx = (
        getattr(request, "request_context", None) if request else None
    )
    if isinstance(_payload_ctx, dict):
        for key, value in _payload_ctx.items():
            rc.setdefault(key, value)

    # ── Phase 4: security-critical fields (set last — cannot be overwritten)──
    rc["session_id"] = getattr(ctx, "session_id", "") or ""
    rc["agent_id"] = getattr(ctx, "agent_id", "") or ""
    rc["root_session_id"] = getattr(ctx, "root_session_id", "") or ""
    rc["root_agent_id"] = getattr(ctx, "root_agent_id", "") or ""
    rc["channel"] = (
        (getattr(request, "channel", None) or "") if request else ""
    )
    # user_id from request is the fallback; metadata-supplied user_id takes
    # precedence if present (set in Phase 1 via setdefault-adjacent merge).
    if "user_id" not in rc:
        rc["user_id"] = (
            (getattr(request, "user_id", None) or "") if request else ""
        )

    _ws = getattr(ctx, "workspace_dir", None)
    if _ws is not None:
        rc.setdefault("workspace_dir", str(_ws))

    app_services = getattr(ctx, "app_services", None)
    if app_services is not None:
        rc.setdefault(
            "approval_coordinator",
            getattr(app_services, "approval_coordinator", None),
        )
        rc.setdefault(
            "tool_coordinator",
            getattr(app_services, "tool_coordinator", None),
        )

    rc["_channel_instance"] = getattr(
        request,
        "channel_instance",
        None,
    )

    # ── Publish to ContextVar so tool functions can read it ──
    set_current_request_context(rc)

    return rc
```

- [ ] **Step 4: Verify import and syntax**

Run:
```bash
cd /Users/zhangsan/github/QwenPaw && python -c "from qwenpaw.runtime.builder import AgentBuilder, _validate_metadata_size; print('OK')"
```

Expected: `OK` printed, no ImportError.

- [ ] **Step 5: Verify metadata size validation works**

Run:
```bash
cd /Users/zhangsan/github/QwenPaw && python -c "
from qwenpaw.runtime.builder import _validate_metadata_size
# Small metadata should pass
_validate_metadata_size({'user_id': 'test'})
print('small: OK')
# Large metadata should raise
try:
    _validate_metadata_size({'big': 'x' * 100 * 1024})
    print('large: FAIL — no exception')
except ValueError as e:
    print(f'large: OK — {e}')
"
```

Expected:
```
small: OK
large: OK — AgentRequest.metadata too large: ...
```

- [ ] **Step 6: Commit**

```bash
git add src/qwenpaw/runtime/builder.py
git commit -m "feat(builder): metadata-first merge in _build_request_context with security hardening

- Merge AgentRequest.metadata before setting critical fields
- Block _-prefixed internal keys
- Enforce 64 KiB metadata size limit
- Set current_request_context ContextVar for tool functions

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: Shell tool — env var injection + output sanitization

**Files:**
- Modify: `src/qwenpaw/agents/tools/shell.py`

**Interfaces:**
- Consumes: `get_current_request_context()` from `qwenpaw.config.context` (Task 1)
- Produces: `_env_var_name(key)` helper, `_sanitize_env_values(output, env)` helper
- Modifies: `execute_shell_command()` — injects QWENPAW_* env vars, sanitizes output

- [ ] **Step 1: Add import for `get_current_request_context`**

In the existing import block at line 21–25, add `get_current_request_context` to the imports from `...config.context`:

Change:
```python
from ...config.context import (
    get_current_shell_command_executable,
    get_current_shell_command_timeout,
    get_current_workspace_dir,
)
```

To:
```python
from ...config.context import (
    get_current_request_context,
    get_current_shell_command_executable,
    get_current_shell_command_timeout,
    get_current_workspace_dir,
)
```

- [ ] **Step 2: Add `_env_var_name` helper above `execute_shell_command`**

Insert before the `execute_shell_command` function definition (before line 505):

```python
_ENV_VAR_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _env_var_name(key: str) -> str | None:
    """Return the uppercased key if it is a valid environment-variable
    name, otherwise None.

    Only keys matching ``[A-Za-z_][A-Za-z0-9_]*`` are accepted so that
    arbitrary metadata keys (e.g. ``tenant-id`` or ``../path``) cannot
    produce invalid or dangerous env-var names.
    """
    upper = str(key).upper()
    return upper if _ENV_VAR_KEY_RE.match(upper) else None


def _sanitize_env_values(output: str, injected_env: dict[str, str]) -> str:
    """Replace QWENPAW_* env var values in *output* with ``***REDACTED***``.

    Only inspects keys that start with ``QWENPAW_`` (the prefix used for
    request-context injection).  Long values are truncated to 64 chars
    for the replacement scan to bound worst-case complexity.
    """
    for key, value in injected_env.items():
        if not key.startswith("QWENPAW_"):
            continue
        if not value:
            continue
        # Truncate long values for bounded replacement scan.
        needle = value[:64]
        if needle in output:
            output = output.replace(needle, "***REDACTED***")
    return output
```

- [ ] **Step 3: Inject QWENPAW_* env vars in `execute_shell_command`**

After `env = os.environ.copy()` (line 580), add:

```python
# Inject request_context as QWENPAW_* environment variables for
# CLI tools and SKILL scripts.  Only scalar values with valid
# env-var-safe keys are exposed.
_injected_env: dict[str, str] = {}
rc = get_current_request_context()
if rc:
    for key, value in rc.items():
        if not isinstance(value, (str, int, float, bool)):
            continue
        env_key = _env_var_name(key)
        if env_key:
            env_name = f"QWENPAW_{env_key}"
            env[env_name] = str(value)
            _injected_env[env_name] = str(value)
```

- [ ] **Step 4: Sanitize output before returning ToolChunk — sandbox path**

In the sandbox execution path (around line 623, inside `if result.exit_code == 0:` and the else branch), add sanitization to `result.stdout` and `result.stderr` before assembling `response_text`.

After the existing `result.stderr` access (line 627 and 634), replace the direct use of `result.stdout` and `result.stderr` with sanitized versions.

The change applies to two places in the sandbox path.  Replace the block from line 623–635:

**Before:**
```python
        if result.exit_code == 0:
            response_text = (
                result.stdout or "Command executed successfully (no output)."
            )
            if result.stderr:
                response_text += f"\n[stderr]\n{result.stderr}"
        else:
            parts = [f"Command failed with exit code {result.exit_code}."]
            if result.stdout:
                parts.append(f"\n[stdout]\n{result.stdout}")
            if result.stderr:
                parts.append(f"\n[stderr]\n{result.stderr}")
            response_text = "".join(parts)
```

**After:**
```python
        _sanitize = _sanitize_env_values if _injected_env else (lambda s, _e: s)
        if result.exit_code == 0:
            stdout_s = _sanitize(
                result.stdout or "", _injected_env,
            )
            response_text = stdout_s or "Command executed successfully (no output)."
            if result.stderr:
                stderr_s = _sanitize(result.stderr, _injected_env)
                response_text += f"\n[stderr]\n{stderr_s}"
        else:
            parts = [f"Command failed with exit code {result.exit_code}."]
            if result.stdout:
                stdout_s = _sanitize(result.stdout, _injected_env)
                parts.append(f"\n[stdout]\n{stdout_s}")
            if result.stderr:
                stderr_s = _sanitize(result.stderr, _injected_env)
                parts.append(f"\n[stderr]\n{stderr_s}")
            response_text = "".join(parts)
```

- [ ] **Step 5: Sanitize output before returning ToolChunk — direct path**

The same output assembly pattern appears in the direct (non-sandbox) execution path around line 733.  Apply the same `_sanitize` wrapping to `stdout_str` and `stderr_str`.

Replace the block from line 733–745:

**Before:**
```python
        if returncode == 0:
            if stdout_str:
                response_text = stdout_str
            else:
                response_text = "Command executed successfully (no output)."
            if stderr_str:
                response_text += f"\n[stderr]\n{stderr_str}"
        else:
            parts = [f"Command failed with exit code {returncode}."]
            if stdout_str:
                parts.append(f"\n[stdout]\n{stdout_str}")
            if stderr_str:
                parts.append(f"\n[stderr]\n{stderr_str}")
            response_text = "".join(parts)
```

**After:**
```python
        _sanitize = _sanitize_env_values if _injected_env else (lambda s, _e: s)
        if returncode == 0:
            stdout_s = _sanitize(stdout_str, _injected_env)
            if stdout_s:
                response_text = stdout_s
            else:
                response_text = "Command executed successfully (no output)."
            if stderr_str:
                stderr_s = _sanitize(stderr_str, _injected_env)
                response_text += f"\n[stderr]\n{stderr_s}"
        else:
            parts = [f"Command failed with exit code {returncode}."]
            if stdout_str:
                stdout_s = _sanitize(stdout_str, _injected_env)
                parts.append(f"\n[stdout]\n{stdout_s}")
            if stderr_str:
                stderr_s = _sanitize(stderr_str, _injected_env)
                parts.append(f"\n[stderr]\n{stderr_s}")
            response_text = "".join(parts)
```

- [ ] **Step 6: Verify imports and syntax**

Run:
```bash
cd /Users/zhangsan/github/QwenPaw && python -c "
from qwenpaw.agents.tools.shell import _env_var_name, _sanitize_env_values
# Test _env_var_name
assert _env_var_name('user_id') == 'USER_ID'
assert _env_var_name('USER_NAME') == 'USER_NAME'
assert _env_var_name('tenant-id') is None
assert _env_var_name('../path') is None
assert _env_var_name('') is None
print('_env_var_name: all assertions passed')

# Test _sanitize_env_values
injected = {'QWENPAW_USER_ID': 'john-123', 'QWENPAW_CHANNEL': 'slack'}
output = 'User john-123 from slack channel'
sanitized = _sanitize_env_values(output, injected)
assert 'john-123' not in sanitized
assert '***REDACTED***' in sanitized
print('_sanitize_env_values: all assertions passed')
print('All checks OK')
"
```

Expected: All assertions pass.

- [ ] **Step 7: Commit**

```bash
git add src/qwenpaw/agents/tools/shell.py
git commit -m "feat(shell): inject request_context as QWENPAW_* env vars with output sanitization

- Map request_context scalar entries to QWENPAW_{KEY} environment variables
- Validate env-var key names (reject invalid chars like dashes and dots)
- Sanitize stdout/stderr to redact QWENPAW_* values before LLM sees output

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: End-to-end integration verification

**Files:**
- Read: `src/qwenpaw/runtime/builder.py`, `src/qwenpaw/config/context.py`, `src/qwenpaw/agents/tools/shell.py`
- No new files created

**Interfaces:**
- Consumes: All changes from Tasks 1–3

- [ ] **Step 1: Verify metadata merge ordering**

Run:
```bash
cd /Users/zhangsan/github/QwenPaw && python -c "
from unittest.mock import MagicMock
from qwenpaw.runtime.builder import AgentBuilder

# Simulate a request with metadata trying to overwrite session_id
ctx = MagicMock()
ctx.session_id = 'real-session-123'
ctx.agent_id = 'real-agent-456'
ctx.root_session_id = 'real-root-789'
ctx.root_agent_id = 'real-root-agent'
ctx.workspace_dir = None
ctx.app_services = None
request = MagicMock()
request.channel = 'console'
request.user_id = 'legacy-user'
request.channel_meta = None
request.request_context = None
request.channel_instance = None
# Malicious metadata trying to overwrite session_id
request.metadata = {
    'user_id': 'metadata-user',
    'session_id': 'hijacked-session',
    'agent_id': 'hijacked-agent',
    'tenant': 'acme-corp',
}

ctx.request = request

rc = AgentBuilder._build_request_context(ctx)

# Critical fields must NOT be overwritten by metadata
assert rc['session_id'] == 'real-session-123', f'session_id was overwritten: {rc[\"session_id\"]}'
assert rc['agent_id'] == 'real-agent-456', f'agent_id was overwritten: {rc[\"agent_id\"]}'
assert rc['root_session_id'] == 'real-root-789', f'root_session_id was overwritten: {rc[\"root_session_id\"]}'
# User-owned fields from metadata should be present
assert rc['user_id'] == 'metadata-user', f'user_id from metadata not preserved: {rc[\"user_id\"]}'
assert rc['tenant'] == 'acme-corp', f'custom metadata not preserved: {rc[\"tenant\"]}'
# user_name from channel_meta should work even with metadata present
assert rc['channel'] == 'console'
print('All metadata merge ordering assertions passed')
"
```

Expected: `All metadata merge ordering assertions passed`

- [ ] **Step 2: Verify _-prefixed key blocking**

Run:
```bash
cd /Users/zhangsan/github/QwenPaw && python -c "
from unittest.mock import MagicMock
from qwenpaw.runtime.builder import AgentBuilder

ctx = MagicMock()
ctx.session_id = 's1'
ctx.agent_id = 'a1'
ctx.root_session_id = 'rs1'
ctx.root_agent_id = 'ra1'
ctx.workspace_dir = None
ctx.app_services = None
request = MagicMock()
request.channel = 'console'
request.user_id = 'u1'
request.channel_meta = None
request.request_context = None
request.channel_instance = None
request.metadata = {
    '_internal_key': 'should-be-blocked',
    '_spawn_subagent': True,
    'user_name': 'John',
}

ctx.request = request
rc = AgentBuilder._build_request_context(ctx)

assert '_internal_key' not in rc, '_internal_key leaked into request_context'
assert '_spawn_subagent' not in rc, '_spawn_subagent leaked into request_context'
assert rc['user_name'] == 'John', 'valid metadata blocked'
print('_-prefix key blocking assertions passed')
"
```

Expected: `_-prefix key blocking assertions passed`

- [ ] **Step 3: Verify env var key validation**

Run:
```bash
cd /Users/zhangsan/github/QwenPaw && python -c "
from qwenpaw.agents.tools.shell import _env_var_name

# Valid keys
assert _env_var_name('user_id') == 'USER_ID'
assert _env_var_name('User_Name') == 'USER_NAME'
assert _env_var_name('CHANNEL') == 'CHANNEL'
assert _env_var_name('tenant_123') == 'TENANT_123'
assert _env_var_name('_private') == '_PRIVATE'

# Invalid keys
assert _env_var_name('tenant-id') is None
assert _env_var_name('my.key') is None
assert _env_var_name('../path') is None
assert _env_var_name('key with spaces') is None
assert _env_var_name('') is None
assert _env_var_name('123leadingdigit') is None

print('All env var key validation assertions passed')
"
```

Expected: `All env var key validation assertions passed`

- [ ] **Step 4: Verify output sanitization**

Run:
```bash
cd /Users/zhangsan/github/QwenPaw && python -c "
from qwenpaw.agents.tools.shell import _sanitize_env_values

injected = {
    'QWENPAW_USER_ID': 'john-abc123',
    'QWENPAW_CHANNEL': 'console',
    'QWENPAW_TENANT': 'acme',
}

# Standard case: all values present in output
out1 = 'Processing for user john-abc123 on console in acme org'
s1 = _sanitize_env_values(out1, injected)
assert 'john-abc123' not in s1
assert 'acme' not in s1
assert '***REDACTED***' in s1
assert s1.count('***REDACTED***') == 3, f'Expected 3 redactions, got: {s1}'

# No QWENPAW values in output — should be unchanged
out2 = 'Hello world'
s2 = _sanitize_env_values(out2, injected)
assert s2 == 'Hello world'

# Empty values should be skipped
injected2 = {'QWENPAW_EMPTY': '', 'QWENPAW_OK': 'val'}
out3 = 'val is here'
s3 = _sanitize_env_values(out3, injected2)
assert 'val' not in s3

# Non-QWENPAW env vars should be ignored
injected3 = {'PATH': '/usr/bin', 'QWENPAW_X': 'secret'}
out4 = 'PATH=/usr/bin secret text'
s4 = _sanitize_env_values(out4, injected3)
assert '/usr/bin' in s4  # not redacted
assert 'secret' not in s4  # redacted

print('All output sanitization assertions passed')
"
```

Expected: `All output sanitization assertions passed`

- [ ] **Step 5: Verify size validation**

Run:
```bash
cd /Users/zhangsan/github/QwenPaw && python -c "
from qwenpaw.runtime.builder import _validate_metadata_size

# Within limit
_validate_metadata_size({'user_id': 'test', 'data': 'x' * 1000})
print('within limit: OK')

# At/exceeds limit — should raise
import json
big_value = 'x' * (64 * 1024)  # 64 KiB just for the value
try:
    _validate_metadata_size({'big': big_value})
    print('exceeds limit: FAIL — no exception raised')
except ValueError as e:
    print(f'exceeds limit: OK — {str(e)[:80]}...')

# Non-serializable
try:
    _validate_metadata_size({'fn': lambda: None})
    print('non-serializable: FAIL — no exception raised')
except ValueError as e:
    print(f'non-serializable: OK — {str(e)[:80]}...')
"
```

Expected: All three cases pass with expected results.

- [ ] **Step 6: Run existing test suite to verify no regressions**

Run:
```bash
cd /Users/zhangsan/github/QwenPaw && python -m pytest tests/ -x -q --timeout=60 2>&1 | tail -20
```

Expected: No new failures introduced by the changes.  (Pre-existing failures, if any, are out of scope.)

- [ ] **Step 7: Commit**

```bash
git commit --allow-empty -m "verify: end-to-end integration checks for user context passthrough

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Self-Review Checklist

- [x] **Spec coverage**: Each spec section maps to a task:
  - 3.1 (metadata merge) → Task 2
  - 3.2 (ContextVar) → Task 1
  - 3.3 (Shell env vars) → Task 3
  - 3.4 (Output sanitization) → Task 3
  - 4 (Security) → Task 2 (ordering + _-blocking) + Task 3 (sanitization + key validation)

- [x] **No placeholders**: All steps contain concrete code, exact bash commands, and expected outputs.

- [x] **Type consistency**: `get_current_request_context()` returns `dict[str, Any] | None` in Task 1 and is consumed with the same signature in Tasks 2 and 3.
