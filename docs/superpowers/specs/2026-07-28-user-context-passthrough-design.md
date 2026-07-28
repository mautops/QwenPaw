# User Context Passthrough — Design Spec

**Date**: 2026-07-28
**Branch**: `feat/user-context-passthrough`
**Status**: draft

## 1. Goal

User identity information (`user_id`, `user_name`, `channel`, and arbitrary
custom metadata) flows from the chat API caller through the Agent runtime to
Tools, MCP Drivers, and Skill CLI subprocesses — programmatically, without the
LLM ever seeing it.

## 2. Data Flow

```
Console / Channel
  │
  │  AgentRequest.metadata = {user_id, user_name, channel, ...custom fields}
  ▼
AgentBuilder._build_request_context()
  │  rc = {user_id, channel, session_id, root_session_id, ...}  ← existing
  │  rc.update(AgentRequest.metadata)                            ← NEW
  ▼
  ├─ ContextVar: set_current_request_context(rc)
  │
  ├─ PolicyGuardedTool._qp_request_context = rc
  │     └─ Tool Python code reads via get_current_request_context()
  │
  ├─ DriverCapabilityTool._request_context = rc
  │     └─ MCP subject constructed from rc["user_id"] → "user:<id>"
  │
  └─ Shell tool (execute_shell_command)
        └─ Child process env: QWENPAW_{KEY} per request_context entry
           CLI / SKILL reads via os.environ / $QWENPAW_USER_ID
```

## 3. Changes

### 3.1 Entry: Merge metadata into request_context

**File**: `src/qwenpaw/runtime/builder.py` — `_build_request_context()`

Metadata is merged **before** security-critical fields are set, so callers cannot
overwrite `session_id`, `agent_id`, `root_session_id`, etc.

```python
# Merge AgentRequest.metadata first (user-owned fields).
# Reject oversized payloads to prevent env-var DoS.
_MAX_METADATA_JSON_BYTES = 64 * 1024  # 64 KiB

_request_metadata = getattr(request, "metadata", None) if request else None
if isinstance(_request_metadata, dict):
    _validate_metadata_size(_request_metadata)
    for key, value in _request_metadata.items():
        if not key.startswith("_"):      # block _-prefixed internal keys
            rc[key] = value

# Security-critical fields are set AFTER the merge so they cannot be
# overwritten by caller-supplied metadata.
rc["session_id"] = getattr(ctx, "session_id", "") or ""
rc["agent_id"] = getattr(ctx, "agent_id", "") or ""
rc["root_session_id"] = getattr(ctx, "root_session_id", "") or ""
rc["root_agent_id"] = getattr(ctx, "root_agent_id", "") or ""
# ... (remaining existing fields set the same way)
```

> **Change summary**: Move the initial `rc` dict literal construction to this
> two-phase pattern: metadata merge first, then critical field assignment.

### 3.2 ContextVar: Unified read access for tool functions

**File**: `src/qwenpaw/config/context.py`

Add ContextVar, getter, and setter:

```python
# Context variable for per-request passthrough context.
current_request_context: ContextVar[dict[str, Any] | None] = ContextVar(
    "current_request_context", default=None
)


def get_current_request_context() -> dict[str, Any] | None:
    """Get the current request context dict, or None if not set."""
    return current_request_context.get()


def set_current_request_context(ctx: dict[str, Any] | None) -> None:
    """Set the current request context dict."""
    current_request_context.set(ctx)
```

**File**: `src/qwenpaw/runtime/builder.py` — `_build_request_context()`

At the end of the method, just before `return rc`, add:

```python
set_current_request_context(rc)
```

> **Timing note**: `_build_request_context()` runs inside `AgentBuilder.build()`
> (phase "fixed 2 build agent"), which is AFTER the `PRE_DISPATCH` hooks.
> Setting the ContextVar here ensures it is populated before any tool executes.

### 3.3 Shell tool: Inject into child process environment

**File**: `src/qwenpaw/agents/tools/shell.py` — `execute_shell_command()`

After `env = os.environ.copy()` (line 580), add:

```python
# Inject request_context as QWENPAW_* environment variables for CLI/SKILL.
# Only scalar values with valid env-var-safe keys are exposed.
rc = get_current_request_context()
if rc:
    for key, value in rc.items():
        if not isinstance(value, (str, int, float, bool)):
            continue
        env_key = _env_var_name(key)
        if env_key:
            env[f"QWENPAW_{env_key}"] = str(value)
```

Add a helper at module level:

```python
import re as _re

_ENV_VAR_KEY_RE = _re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

def _env_var_name(key: str) -> str | None:
    """Return the uppercased key if it is a valid env-var name, else None."""
    upper = str(key).upper()
    return upper if _ENV_VAR_KEY_RE.match(upper) else None
```

> Keys like `tenant-id` or `../path` are silently skipped — they cannot form
> valid environment variable names.

### 3.4 Output sanitization (required)

After command execution and before returning `ToolChunk`, scan output text for
values of `QWENPAW_*` environment variables and replace occurrences with
`***REDACTED***`. This prevents the LLM from reading user context via the `env`
command.

```python
def _sanitize_env_values(output: str, env: dict[str, str]) -> str:
    """Replace QWENPAW_* env var values in output with ***REDACTED***."""
    for key, value in env.items():
        if key.startswith("QWENPAW_") and value:
            output = output.replace(value, "***REDACTED***")
    return output
```

Called on stdout and stderr before assembling the `ToolChunk` response.

## 4. Security Considerations

| Risk | Severity | Mitigation |
|------|----------|------------|
| Metadata overwrites `session_id`/`agent_id`/`root_session_id` | 🔴 High | Set critical fields AFTER metadata merge (section 3.1) |
| LLM reads user context via `env` command | 🟡 Medium | Output sanitization masks `QWENPAW_*` values in shell output (section 3.4) |
| Invalid env-var keys (`tenant-id`, `../path`) | 🟡 Medium | Env-var-safe key regex filter — invalid keys silently skipped (section 3.3) |
| Metadata too large (DoS via oversized env) | 🟡 Medium | Reject metadata dicts whose JSON serialization exceeds 64 KiB |
| `_`-prefixed internal key injection | 🟡 Medium | Block keys starting with `_` in metadata merge (section 3.1) |
| Client forges identity | 🟢 Low | Existing trust model; not introduced by this change |
| CLI logs env vars to files | 🟢 Low | Caller responsibility; document in usage guide |
| ContextVar leak across requests | 🟢 Low | asyncio task isolation already handles this |

## 5. Unchanged Components

| Component | Reason |
|-----------|--------|
| `AgentRequest` schema | Already has `metadata: Optional[Dict[str, Any]]` |
| `PolicyGuardedTool` | Already stores `_qp_request_context` |
| `DriverCapabilityTool` | Already stores `_request_context` |
| `_subjects_from_context()` | Already reads `user_id` from `request_context` |
| All Channels | `channel_meta` already flows into `request_context` |
| Skill system | Skills execute via Tools, which already carry `request_context` |

## 6. CLI / SKILL Usage

CLI tools and SKILL scripts read user context from environment variables:

```bash
#!/bin/bash
echo "Processing request from $QWENPAW_USER_NAME ($QWENPAW_USER_ID)"
echo "Channel: $QWENPAW_CHANNEL"
echo "Tenant: $QWENPAW_TENANT"       # custom field — automatically exposed
echo "Org: $QWENPAW_ORG"             # custom field — automatically exposed
```

New custom fields added to `AgentRequest.metadata` are automatically exposed as
`QWENPAW_{FIELD_UPPER}` without any code changes.

## 7. Scope Boundaries

**In scope**:
- Console and all messaging channels
- Built-in tools (shell, file_io, browser, etc.)
- MCP Driver tools
- Skill CLI subprocesses

**Out of scope**:
- LLM visibility (explicitly blocked by design)
- User authentication / identity verification
- Changes to channel-specific message parsing
- Metadata schema enforcement (caller-owned)
