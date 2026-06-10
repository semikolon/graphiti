# Graphiti fork vs upstream — divergence + retrieval-feature sequencing (2026-06-10)

**Source**: general-purpose subagent, run 2026-06-10 from the din-mamma session. Answers the gating
question raised before adding retrieval features (MMR / cross-encoder / build_communities / HippoRAG):
build on the fork's 0.20.1, or reintegrate onto upstream 0.29.2 first?

**Verdict**: **Build the retrieval features on the fork's 0.20.1 NOW.** The recipes + reranker clients +
community functions already ship in the fork's vendored `graphiti_core`; the only gap is MCP-server
exposure (hours, additive, near-zero conflict). Cross-encoder is even already live (auto-init
`OpenAIRerankerClient`). Reintegration (0.20.1 → 0.29.x) is real debt (79 ahead / 237+ behind) but
**orthogonal** — it would NOT unlock these features (upstream's MCP also wires only RRF + NODE_DISTANCE),
and a naive rebase would risk the fork's still-unmerged reliability patches (#1176, #1164 OPEN upstream).
Schedule reintegration as a separate, deliberately-scoped **rebuild-on-upstream** project; cherry-pick the
0.29.1 attribute-hallucination guards + 0.28.2 Cypher-injection hardening opportunistically. HippoRAG/PPR
is genuinely net-new (absent from both fork and upstream) — separate research spike, lowest priority.

The full verbatim report follows.

---

# Graphiti Fork vs Upstream: Divergence Analysis & Retrieval-Feature Sequencing Decision

## TL;DR verdict

**Build the retrieval features (MMR, cross-encoder, build_communities) on the fork's 0.20.1 NOW — option (a).** They are cheap MCP-exposure work over capability the fork's `graphiti_core` **already ships**, with near-zero conflict risk. Do **NOT** gate this behind a 0.20.1→0.29.x reintegration. Schedule the reintegration as a **separate, later, deliberately-scoped project** (it's real debt — 79 ahead / 237+ behind — but it's orthogonal to these features and would only delay them while risking the fork's still-unique reliability patches). HippoRAG/PPR is genuinely net-new (absent from both fork and upstream) — treat as a separate research spike, lowest priority.

---

## 1. Fork divergence catalog (file-path + git evidence)

**Divergence geometry** (`git merge-base` + `rev-list`):
- Merge-base: `eeb0d87` (`update (#891)`, **2025-09-03**)
- Fork branch `feature/explicit-scoping`: **79 commits ahead**, **237 behind** our stale `upstream/main` (which itself is fetched only to 2026-04-22 `9cdcc93`; true upstream is 0.29.2, 2026-06-08 — so the real "behind" count is larger)
- `graphiti_core/`: **1,769 lines diverged across 25 files**
- `mcp_server/`: **6,432 lines diverged across 30 files** (the heavy customization lives here)

### Fork patches in `graphiti_core/` — reliability layer (the crown jewels)

| Patch | Commit | File | Upstream status (gh-verified) |
|---|---|---|---|
| Remove `asyncio.wait_for` (httpx-pool corruption, httpcore #961) + worker-task GC strong-refs | `ae9dfe5` | `graphiti.py`, queue worker | **#1176 still OPEN upstream** → **fork-only** |
| RediSearch reserved-word sanitizer (MAP/REDUCE/FILTER/APPLY/LIMIT/…) | `8fb9934`, `c4e4e50` | `helpers.py` | No upstream equivalent → **fork-only** |
| `output_text.refusal` Responses-API type guard | `10a7390` | `openai_base_client.py` | **fork-only** |
| Truncation detect + double-max_tokens retry | `9331410` | `openai_client.py`, `anthropic_client.py` | **fork-only** |
| Attribute field-collision sanitize (#1164) | `f49c982` | `node_operations.py` | **#1164 still OPEN upstream** → **fork-only** |
| FalkorDB bulk-edge `source/target_node_uuid` (#1001) | `30f9725` | `edge_db_queries.py` | **#1001 CLOSED upstream 2025-11-17** → now redundant-with-upstream |
| Swedish diacritics (åäö) preservation | `f70bc10` | `extract_nodes.py` prompt | **fork-only** |
| First-time-edge #1242 attribute fix (one-line gate) | `dd2e880` | `edge_operations.py` | Backport of upstream 0.28.1 behavior |
| `@handle_multiple_group_ids` + graph-per-group (ported from upstream `c144ff5`) | `bcb07b8` | `decorators.py`, `graphiti.py` | Ported-from-upstream |
| FallbackEmbedder (local llama.cpp + OpenAI fallback) | `9b1424b` | `embedder/fallback.py` | **fork-only** (Darwin GTX 1650 local embeddings) |
| In-memory embedding cache | `1a59710` | `search/search.py` | **fork-only** |
| Adaptive chunking (ported PR #1129) + wire into `extract_nodes` | `842c818`, `4a1e974` | `content_chunking.py` (826 lines) | Ported-from-upstream |
| Community fixes (#1276 dedup-scaling, #1086 LPA oscillation, #1420 batch projection, #1398 max_coroutines) | `6d0b7d5`, `ecfce00`, `eed3d85`, `99e88f2` | `community_operations.py`, `node_operations.py` | Ported/manual; some still OPEN upstream |

### Fork patches in `mcp_server/` — the divergence that makes rebase hard

- **Explicit project/global/cross-project scoping** — `search_nodes`/`search_facts`/`search_global_*`/`search_cross_project_*` (fork-only architecture; upstream uses a single per-server group model)
- **`raw_cypher_query`** (read-only fenced) + **`cypher_query_write`** (`ac14d92`, write-capable, blocks DROP only, 10K cap) — **fork-only**
- **Generic edge ontology** `MonetaryObligation`/`MonetaryTransfer`/`TemporalDeadline` + `EDGE_TYPE_MAP` (`dd2e880`, 2026-06-09) — fork-only finance wiring
- **`notifications.py`** (393 lines, Hammerspoon/ntfy + `insufficient_quota` vs rate-limit classification) — fork-only
- **`get_recent_errors`** + `-32602` sentinel-default fixes (`3a45266`, `d111b1d`) — fork-only
- **FalkorDBLite singleton runtime** + launcher DB-mode ownership + `export_falkordblite_runtime.py` — fork-only
- **custom_entities.py** (380 lines: Decision, Pattern, Library, Task w/ external_source/id, Person, Commitment, Meeting) — fork-only
- ~2,800 lines of fork-only tests (scoping, cypher, notifications, concurrency, first-time-edge)

**Net**: the fork's value is concentrated in (1) reliability patches in `graphiti_core/` that are **still unmerged upstream** (#1176, #1164 OPEN), and (2) a heavily-customized MCP server. Both are exactly what a naive rebase would endanger.

## 2. What upstream 0.29.2 shipped that we lack & would want

Evidence: GitHub releases page + `mcp_server/README.md` + `src/config/schema.py` (gh-fetched from `main`).

| Version | Feature we lack | Want it? |
|---|---|---|
| 0.28.1 | First-time-edge attribute extraction (#1242) | **Already backported** (`dd2e880`) |
| 0.28.2 | Cypher-injection hardening of search filters; diskcache→sqlite CVE | Yes (security) — port-candidate |
| **0.29.0** | **Combined node+edge extraction** (1 LLM call for both); decoupled timestamp resolution; multi-episode batch extraction; `summarize_saga()` | Mixed — efficiency win, but **collides hardest with fork's reflexion/chunking** (`node_operations.py` = 205 fork-added lines) |
| **0.29.1** | **Attribute-hallucination guards** + `cap_string_attributes` (>250-char drop) | **Yes** — directly relevant to the finance edge-ontology just wired (`edge_types_attribute_gap_2026_06_09.md` flags this as the deferred-recommendation-2 payoff) |
| **0.29.2** | **MCP core-parity #1553**: `build_communities` tool, `add_triplet`, sagas, temporal filters, **`edge_types`/`edge_type_map` config schema** (`EdgeTypeConfig`/`EdgeTypeMapEntry`), gpt-5.5 default | **Partially** — upstream now exposes `build_communities` + edge-type config as a divergent shape from our fork's |

**Crucial nuance for the decision**: I fetched upstream `main`'s MCP search service (`mcp_server/src/graphiti_mcp_server.py:519-528`). **Upstream's MCP also only wires RRF + NODE_DISTANCE** for search — it does **NOT** expose MMR or cross-encoder recipe selection as tools either. So reintegrating to upstream would **not** hand us the MMR/cross-encoder retrieval features for free. That work is net-new regardless of which base we're on.

## 3. THE DECISION — sequencing verdict

### Recommendation: **(a) build on the fork's 0.20.1 now**, reintegration as a separate later project.

**Considered:**
- **(b) Rebase/reintegrate onto 0.29.x FIRST, then add features** — *rejected as the gate*: (i) it does NOT unlock the headline features (upstream MCP lacks MMR/cross-encoder exposure too — §2); (ii) the highest-conflict zone is `node_operations.py` extraction (fork +205 lines reflexion+chunking) vs upstream's 0.29.0 combined-extraction rewrite — a genuine multi-day merge with regression risk to a *live Darwin production daemon*; (iii) it risks silently dropping fork-only reliability patches that are **still OPEN upstream** (#1176 worker-GC, #1164 attribute-collision) — exactly the patches that cost 5 sessions + a 2-month stealth failure to earn. Gating cheap, isolated retrieval work behind this is the tail wagging the dog.
- **(c) Cherry-pick specific upstream commits into the fork** — *accept selectively, but NOT as a gate*: this is the right vehicle for the **0.29.1 attribute-hallucination guards + `cap_string_attributes`** (small, self-contained, directly benefits the new finance edge ontology) and the 0.28.2 Cypher-injection hardening. Do these opportunistically; they don't block retrieval features.
- **(a) build on fork 0.20.1 now** — *chosen*: the retrieval features are **MCP-exposure of capability the fork's core already ships** (proven in §4). The fork's search-layer patches are tiny and localized (`search.py`/`search_config.py`/`search_utils.py` — group-id scoping + `SearchResults.merge` only, no reranker-path changes), so adding recipe-selecting tools is near-zero-conflict. Effort is hours, not days; risk is contained to new tools.

### Why this is the wise call
The features and the reintegration are **orthogonal**. The features cost hours on the fork and carry contained risk. The reintegration costs days, must be deliberately scoped (per the Mar-15 doc's own "controlled re-integration project, not casual cherry-picking" framing), and — critically — does **not** advance the features. Coupling them would convert an hours-of-work win into a days-of-work-with-production-risk blocker. Decouple.

### Migration sketch (for when reintegration IS scheduled — separate session)
This is the deferred-but-real path, not a now-task:
1. **Re-fetch upstream** (local `upstream/main` is stale at 2026-04-22; tag `v0.29.2`).
2. **Classify each fork `graphiti_core/` patch** against current upstream: (a) now-upstreamed (#1001 → drop), (b) still-fork-only-and-needed (#1176, #1164, RediSearch sanitizer, FallbackEmbedder, diacritics → must survive), (c) ported-from-upstream that upstream has since evolved (chunking, community fixes → re-derive on upstream).
3. **Strategy: rebuild-on-upstream, not rebase-fork-onto-upstream.** Start from `v0.29.2`, re-apply the ~6 still-unique reliability patches as fresh small commits (each is <70 lines), re-port the FalkorDBLite singleton + explicit-scoping MCP layer onto upstream's new modular `src/` MCP package, and converge edge-type config onto upstream's `EdgeTypeConfig`/`EdgeTypeMapEntry` schema. The MCP server is a near-rewrite either way (upstream went modular) — accept that and treat the fork's MCP as a reference, not a merge source.
4. **Highest-risk zone**: `node_operations.py` extraction (reflexion+chunking) vs 0.29 combined-extraction. Decision point at that time: adopt upstream combined-extraction and **retire** the fork's reflexion path, or keep reflexion as opt-in. Gate behind the fork's 266-test suite + a live Darwin smoke run.
5. **Pin floor ≥ 0.29.1** to get first-time-edge (#1242, already have) + hallucination guards + `cap_string_attributes`.

## 4. Confirmation: MMR / cross-encoder / build_communities already exist in fork 0.20.1 core

**Yes — all three ship in the fork's vendored `graphiti_core` 0.20.1. They are cheap to expose NOW. No upstream needed.** Evidence:

- **Search recipes** — `graphiti_core/search/search_config_recipes.py` ships the full matrix: `COMBINED_HYBRID_SEARCH_RRF/MMR/CROSS_ENCODER`, and per-type `EDGE_/NODE_/COMMUNITY_HYBRID_SEARCH_{RRF,MMR,NODE_DISTANCE,EPISODE_MENTIONS,CROSS_ENCODER}`.
- **Reranker implementations** — `search/search_utils.py`: `rrf` (1749), `node_distance_reranker` (1767), `episode_mentions_reranker` (1821), `maximal_marginal_relevance` (1854).
- **Cross-encoder clients** — `graphiti_core/cross_encoder/`: `bge_reranker_client.py`, `gemini_reranker_client.py`, `openai_reranker_client.py`. The `Graphiti` constructor (`graphiti.py:205-208`) **auto-initializes `OpenAIRerankerClient()`** when none is passed — and the MCP server calls `Graphiti(...)` (line 828) without one, so **a cross-encoder is already live**; a cross-encoder search tool needs zero new infra.
- **build_communities** — `community_operations.py`: `build_communities` (289), `build_community` (241), `label_propagation` (99, with the fork's oscillation fixes), `remove_communities`, `determine_entity_community`. With the `max_coroutines` threading already ported.

**The only gap is MCP-server exposure.** The fork's MCP wires just `NODE_HYBRID_SEARCH_RRF` + `NODE_HYBRID_SEARCH_NODE_DISTANCE` (imports at `graphiti_mcp_server.py:64-67`); `search_facts` uses the convenience `client.search()` (default `EDGE_HYBRID_SEARCH_RRF`); and there is **no `build_communities` MCP tool** (the 18-tool inventory has none). So the work is: add tools that pass `*_MMR` / `*_CROSS_ENCODER` recipes to `client._search()`, and add a `build_communities` tool wrapping the existing core function — all additive, in the heavily-forked-but-test-covered `mcp_server/` layer.

**HippoRAG / Personalized-PageRank**: `grep` across `graphiti_core/` (excluding `.venv`) returns **nothing** for `pagerank|hipporag|ppr|personalized`. It exists in **neither** fork 0.20.1 nor upstream 0.29.2's released core. Genuinely net-new — a separate research spike (would need a PPR pass over the FalkorDB graph + a new reranker/search-method), lowest priority, and explicitly NOT unlocked by reintegration.

---

### Key files for follow-up
- Fork core capability: `graphiti_core/search/search_config_recipes.py`, `search/search_utils.py`, `cross_encoder/`, `utils/maintenance/community_operations.py`
- MCP exposure gap: `mcp_server/graphiti_mcp_server.py` (imports L64-67; 18 `@mcp.tool()` defs)
- Reliability-patch provenance: `~/dotfiles/docs/graphiti_upstream_review_2026_03_15.md` § "Fork-Specific Reliability Fixes"
- Edge-ontology + version-lag rationale: `docs/edge_types_attribute_gap_2026_06_09.md`
- Reintegration prior decisions: `~/dotfiles/docs/graphiti_upstream_sync_assessment_2026_04_18.md` ("don't rebase, cherry-pick"), `~/dotfiles/docs/graphiti_community_fixes_session_2026_04_24.md`

**Sources:** getzep/graphiti releases; upstream MCP README; issues #1176 (OPEN), #1164 (OPEN), #1001 (CLOSED).
