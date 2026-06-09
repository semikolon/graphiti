# Sentinel-default regression: empty-string sentinel breaks `is not None` behavioral checks

**Date**: 2026-06-09
**Affected tools**: `search_nodes`, `search_global_nodes`, `search_cross_project_nodes`
**Severity**: Functional — every call **without** an explicit `center_node_uuid` errored with
`Error searching nodes: No center node provided for Node Distance reranker`.
**Introduced by**: commit `3a45266` (sentinel-default all nullable-optional tool params, the `-32602` fix).
**Caught by**: live tool test immediately after deploying the `-32602` fix (a `search_nodes` call returned the reranker error instead of results).

## What happened

The `-32602` fix replaced the nullable optional `center_node_uuid: str | None = None` with the
empty-string sentinel `center_node_uuid: str = ""` (FastMCP rejects nullable optionals on the
transport — see `mcp_get_recent_errors_invalid_params_2026_06_04.md`). At the **passthrough**
use-site this was correctly coerced back:

```python
center_node_uuid=center_node_uuid or None,   # "" -> None, correct
```

But the **behavioral** reranker-selection check a few lines above was missed:

```python
if center_node_uuid is not None:                          # <-- BUG
    search_config = NODE_HYBRID_SEARCH_NODE_DISTANCE...    # needs a real center node
else:
    search_config = NODE_HYBRID_SEARCH_RRF...              # no center node needed
```

With the sentinel, `center_node_uuid` is `""` (not `None`) on a no-center call, so
`"" is not None` is **True** → the code selects the Node-Distance reranker, which then fails
inside graphiti because no center node was actually provided.

## Root cause / generalizable lesson

When converting a nullable optional (`X | None = None`) to a transport-safe sentinel
(`str = ""`, `dict = {}`), coercing at passthrough use-sites (`x or None`) is **not sufficient**.
You must also update every **behavioral branch** that distinguished `None` from a value:

| Pattern | Sentinel `""` behaviour | Fix |
|---|---|---|
| `if x is not None:` | `"" is not None` → **True** (wrong) | `if x:` (truthiness) |
| `if x is None:` | `"" is None` → **False** (wrong) | `if not x:` |
| `x == None` / `x != None` | same trap | truthiness |
| `x or None` (passthrough) | `"" or None` → `None` (correct) | already fine |

The empty-string sentinel is falsy, so a **truthiness** check (`if x:` / `if not x:`) restores the
original None-means-absent intent. The `dict = {}` sentinel has the same property (`{}` is falsy),
so `if params:` / `if not params:` is the right form there too.

## The 3 affected sites + fix

`graphiti_mcp_server.py` lines 1239 (`search_nodes`), 1319 (`search_global_nodes`),
1509 (`search_cross_project_nodes`):

```diff
-        if center_node_uuid is not None:
+        if center_node_uuid:
             search_config = NODE_HYBRID_SEARCH_NODE_DISTANCE.model_copy(deep=True)
         else:
             search_config = NODE_HYBRID_SEARCH_RRF.model_copy(deep=True)
```

Not affected: line 1001 `effective_group_id is not None` — that is on the **resolved** group id
returned by `get_effective_group_id()` (always a `str`, never a sentinel), not on a tool param.
`if valid_at:` and `if params is None:` were already truthiness / inverse-safe.

## Prevention

When sentinel-defaulting a param in future, grep the function body for **every** reference to that
param, not just the one passed downstream:

```sh
grep -nE "<param> is (not )?None|<param> ==|<param> !=" graphiti_mcp_server.py
```

## Cross-references

- `-32602` root-cause + the sentinel pattern: `docs/mcp_get_recent_errors_invalid_params_2026_06_04.md`
- Sentinel commit that introduced this: `3a45266`
- Fix commit: (this change)
