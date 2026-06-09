# MCP Tool `get_recent_errors` returns `-32602 Invalid request parameters`

> **✅ RESOLVED 2026-06-09 (comprehensively).** The root cause is confirmed: FastMCP
> rejects nullable-optional params (`X | None = None` → JSON-Schema `anyOf:[type,null]`)
> with `-32602`, and a single poisoned request breaks the SSE session for all
> subsequent calls (which is why even uuid-only tools failed after a `raw_cypher_query`).
> Two-layer fix shipped:
> 1. **Sentinel pattern applied to ALL 11 remaining affected tools** (not just
>    `get_recent_errors`): `str | None = None → str = ""`, `dict | None = None → {}`,
>    with `or None` coercion at use-sites. Commit `3a45266`.
> 2. **mcp SDK upgraded 1.26.0 → 1.27.2** (`9e5b053`) + the previously-undeclared
>    `falkordb` runtime dep fixed (`d136a6a`, it was hand-installed and pruned by
>    `uv sync`). Daemon redeployed + import-gated; full unit suite green.
>
> The sentinel pattern is the durable defensive layer (works across MCP clients +
> SDK versions); the upgrade is housekeeping. Original diagnosis retained below.

**Date**: 2026-06-04
**Affected tool**: `mcp__graphiti__get_recent_errors` (FastMCP-registered tool in `mcp_server/graphiti_mcp_server.py:1858-1875`)
**Severity**: Functional — the tool cannot be invoked from Claude Code, blocking the documented "review errors that occurred while you were away or in Focus mode" use case.

## Reproduction

Calling the tool from CC via the Graphiti MCP connection (`claude_ai_Graphiti`) returns JSON-RPC error code `-32602 Invalid request parameters` in every variation tried:

```jsonc
// No arguments (use defaults: since_minutes=60, error_type=None)
{}                                                            // → -32602

// since_minutes only
{"since_minutes": 2880}                                       // → -32602

// since_minutes + error_type
{"since_minutes": 2880, "error_type": "episode_processing"}   // → -32602
```

The server-side signature (`graphiti_mcp_server.py:1858-1875`) has defensible defaults and an `error_type: str | None = None` optional argument; the unit tests in `mcp_server/test_notifications.py` exercise `get_recent_errors_list()` directly (not via the MCP transport) and pass — so the underlying function is fine; the failure is in the FastMCP tool-binding / JSON-Schema layer.

## Suspected root cause

Likely a FastMCP schema-coercion issue with `str | None` (Python 3.10+ union syntax) → JSON-Schema `anyOf: [string, null]` round-trip. CC's MCP client sends `null` (or omits the key entirely), and FastMCP appears to reject it with `-32602` rather than apply the default.

The recent fork commit `dae3d4c docs(claude): cypher_query_write cap 500→10000 + FastMCP response gotcha` already documents a different FastMCP behaviour pitfall — there may be a related parameter-coercion gotcha worth surfacing in the same place.

## Suggested investigation

1. Reproduce locally with the FastMCP CLI client to confirm the failure happens at the transport layer (not in CC's MCP client).
2. Try replacing `error_type: str | None = None` with `error_type: Optional[str] = None` (the older `typing.Optional` form) — some FastMCP versions handle the legacy form differently from `|`-union.
3. Try adding `Annotated[str | None, ...]` with a Field description — FastMCP may need an explicit field marker for nullable optionals.
4. Confirm the FastMCP version in `mcp_server/pyproject.toml` (or wherever pinned) and check upstream FastMCP changelog for known `Optional[str]` JSON-Schema fixes.
5. As a workaround until fixed: change the signature to `error_type: str = ""` (empty-string sentinel) and treat the empty string as "no filter" internally — cleaner than fighting FastMCP's null handling.

## Impact on consumers

Any CC session that needs to review Graphiti background processing errors (the documented "Focus mode" use case) cannot do so via this tool. Operators currently fall back to reading `~/.graphiti/logs/mcp-server.log` directly (per the global CLAUDE.md `Knowledge Infrastructure Reliability` section).

## Cross-references

- Tool registration: `mcp_server/graphiti_mcp_server.py:1858-1875`
- Implementation: `mcp_server/notifications.py:111` (`get_recent_errors_list`)
- Unit-test coverage (underlying function): `mcp_server/test_notifications.py`
- FastMCP response-gotcha precedent: commit `dae3d4c` (docs/claude/, `cypher_query_write` 500→10000 + FastMCP gotcha)
- Global Graphiti infrastructure context: `~/.claude/CLAUDE.md` § *Knowledge Infrastructure Reliability*
