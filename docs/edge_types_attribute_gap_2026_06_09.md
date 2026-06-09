# Structured edge attributes: the fork doesn't wire `edge_types` (use-existing, not build)

**Date**: 2026-06-09
**Status**: ✅ **Implemented 2026-06-09** — recommendation 1 (generic edge ontology wired into the MCP
server) + the first-time-edge attribute bug (#1111/#1242) fixed surgically. Recommendation 2 (the full
graphiti-core 0.20.1 → 0.29.x upgrade) remains a separate deferred project. The analysis below is retained
as the rationale; see "Implementation (shipped)" at the end.
**Severity**: Functional limitation (now fixed) — every edge this fork's MCP server wrote had
`attributes == {}`; all structured data (amounts, dates, OCR numbers) survived only as free-text inside the
`.fact` string, so it could not be queried/filtered structurally.

## Observation

A din-mamma episode (Swedish myndighetspost financial review, 2026-06-08) extracted 7 facts with high
fidelity *in the fact text* — e.g. `CREDITOR_OF`: "…outstanding amount 32377 SEK, monthly amount 519 SEK,
due date 2026-04-30." But every one of those 7 edges has `attributes: {}`. The numbers are narrated, never
structured. "Find all debts > 10 000 SEK" therefore requires NL search + string parsing, not a graph query.

Entity-side extraction, by contrast, *did* produce structured attributes: the `ExternalConstraint` nodes
(Försäkringskassan, Skatteverket, SCB) carry `constraint_type`, `compliance_status`, `effective_date`,
`review_date`. So the asymmetry is **node attributes populated, edge attributes empty** — and the cause is
purely that we wire entity types but not edge types.

## Root cause (in this fork)

`graphiti_mcp_server.py` passes `entity_types` to `add_episode` but never `edge_types` / `edge_type_map`:

```python
# lines ~1043 and ~1174 (add_memory / add_global_memory)
entity_types = ENTITY_TYPES if config.use_custom_entities else {}
await client.add_episode(
    ...,
    entity_types=entity_types,        # ← entity ontology wired
    # edge_types=...                   ← MISSING
    # edge_type_map=...                ← MISSING
)
```

There is an `ENTITY_TYPES` dict (line ~164) but no `EDGE_TYPES` / `EDGE_TYPE_MAP`. With no edge-type
candidates, `graphiti_core.utils.maintenance.edge_operations.resolve_extracted_edge` keeps the generic
relation and explicitly sets `resolved_edge.attributes = {}`. Structured edge attributes appear **only**
when a custom edge type *with fields* is defined and wired into `edge_type_map` for the entity-pair.

## Upstream capability — this is USE-EXISTING, not BUILD

`Graphiti.add_episode()` has long accepted:

```python
edge_types: dict[str, type[BaseModel]] | None = None
edge_type_map: dict[tuple[str, str], list[str]] | None = None   # (src_label, tgt_label) -> [edge_type_name,...]
```

Each field on a custom edge Pydantic model becomes a key in the resulting `EntityEdge.attributes`. The
official docs' canonical example is literally finance-shaped:

```python
class Investment(BaseModel):
    amount: Optional[float]            = Field(None, description="Investment amount in USD")
    investment_type: Optional[str]     = Field(None, description="equity, debt, etc.")
    investment_date: Optional[datetime] = Field(None, description="Date of investment")
```

Best-practice rules (Zep docs, "Custom Entity and Edge Types"): use `datetime`/`int`/`float` not strings;
keep attributes atomic (`amount` + `currency` + `due_date`, not one packed string); all `Optional`;
`Field(description=...)` drives extraction; `Field(max_length=...)` caps noisy string fields.

## Version lag (the load-bearing housekeeping)

This fork runs **graphiti-core 0.20.1**; upstream is **0.29.2** (PyPI, 2026-06-08). Edge-relevant evolution
we are missing:

| Version | Date | Edge change we lack |
|---|---|---|
| 0.28.1 | 2026-02-17 | **#1242 — extract custom edge attributes on FIRST-time edge ingestion.** Before this, a brand-new edge (no prior similar edge) early-returned with `attributes = {}`. For a debt graph the *first* `OwesDebt` per creditor would lose its amount/date. **Hard floor: pin ≥ 0.28.1.** |
| 0.29.0 | 2026-04-27 | Combined node+edge extraction (opt-in); decoupled timestamp resolution (`valid_at`/`invalid_at` as a dedicated step); attribute dicts no longer clobber first-class edge fields. |
| 0.29.1 | 2026-05-21 | **#1498 — attribute-hallucination guards** + `cap_string_attributes` (drops string attrs >250 chars). Matters precisely for `amount`/`due_date` fields (prevents a reasoning-dump landing in an attribute). |
| 0.29.2 | 2026-06-08 | **#1553 `feat(mcp): core-parity … custom types`** — the *official* MCP server now exposes per-server `edge_types`/`edge_type_map` config via `build_edge_types` / `build_edge_type_map`. |

## Recommendation (ranked)

1. **Wire edge types in the MCP server** (small, self-contained): add `EDGE_TYPES` + `EDGE_TYPE_MAP`
   dicts alongside `ENTITY_TYPES`, pass `edge_types=` / `edge_type_map=` in both `add_episode` calls,
   gated by `use_custom_entities` (or a sibling flag). Prefer converging on upstream #1553's
   `build_edge_types` / `build_edge_type_map` config shape rather than a divergent path.
2. **Reconcile graphiti-core 0.20.1 → ≥ 0.29.1** (larger, separate project): gets the first-time-edge fix
   (#1242) + hallucination guards (#1498). ⚠️ This fork carries reliability patches in `graphiti_core/`
   (httpx-pool cancellation, worker-task GC, RediSearch reserved-word sanitizer, `output_text.refusal`
   guard — see the global Knowledge-Infrastructure notes). Before rebasing, check which are already
   upstreamed so they aren't lost. **Not a now-task.**
3. **Determinism caveat for consumers**: edge-attribute extraction is an *LLM* step (extract → validate →
   `apply_capped_attributes`), not deterministic parsing. A consumer needing exact financial values keeps
   its own deterministic parse as the source of truth and treats Graphiti edge attributes as the queryable
   *projection*, not the authoritative ledger.

## Implementation (shipped 2026-06-09)

1. **Generic edge ontology in the MCP server** (`graphiti_mcp_server.py`): `MonetaryObligation`
   (`amount`/`currency`/`due_date`/`recurring_amount`/`ocr`), `MonetaryTransfer`
   (`amount`/`currency`/`direction`/`taxable`), `TemporalDeadline` (`due_date`/`status`) → `EDGE_TYPES`
   + a wildcard `EDGE_TYPE_MAP {('Entity','Entity'): [...]}`. Generic + global, symmetric with
   `ENTITY_TYPES`, gated by `use_custom_entities`; both `add_episode` call sites now pass
   `edge_types=` + `edge_type_map=`. Non-finance graphs are unaffected — an edge only populates these
   when a fact actually carries a sum/date, otherwise it stays a generic relation with `{}`.
2. **First-time-edge fix back-ported** (`graphiti_core/.../edge_operations.py`): the #1242 behaviour
   was ported as a one-line gate rather than a full upgrade — the dedup-skip early-return now only fires
   when there are **no applicable edge types** (`and not edge_types`), so first-time edges fall through to
   type-classification + attribute extraction. Zero behaviour change when no edge ontology is passed.
   Cost: one extra small-model `resolve_edge` call per first-time edge when edge types apply (the
   documented #1242 trade-off). Pinned by `mcp_server/test_first_time_edge_attributes.py` (RED on the old
   early-return, GREEN with the gate).
3. **Still deferred**: recommendation 2 (graphiti-core 0.20.1 → ≥0.29.1) — would additionally bring the
   attribute-hallucination guards (#1498) + `cap_string_attributes`, but requires reconciling this fork's
   `graphiti_core/` reliability patches against upstream first.

**Validated live 2026-06-09** (Darwin daemon code, real LLM + FalkorDB, throwaway group = all first-time
edges → exercises the #1242 fix): a finance episode produced **7/7 edges with populated attributes** —
`MonetaryObligation {amount:32377, currency:'SEK', due_date:2026-04-30, recurring_amount:519}`,
`TemporalDeadline {due_date:2026-05-04, status:'passed'}` (status correctly inferred),
`MonetaryTransfer {amount:2607, currency:'SEK', direction:'inbound'}` (direction correctly inferred).
Known characteristic (not a regression): graphiti emits one edge per fact-sentence, so a single debt can
yield several `MonetaryObligation` edges each carrying the full attributes — queryable, and dedup
consolidates on re-ingestion. A consumer querying by amount/creditor should expect to dedupe.

## Cross-references

- Consumer-side design (the finance edge ontology + ledger-vs-projection boundary):
  `~/Projects/din-mamma/docs/graphiti_memory_layer_design_2026_06_09.md`
- Sibling MCP fork docs: `mcp_sentinel_behavioral_check_regression_2026_06_09.md`,
  `mcp_get_recent_errors_invalid_params_2026_06_04.md`
- Upstream: getzep/graphiti releases v0.27.0→v0.29.2; docs "Custom Entity and Edge Types"
  (help.getzep.com/graphiti/core-concepts/custom-entity-and-edge-types); issue #1111 (first-time-edge),
  PR #1242 / #1498 / #1553.
