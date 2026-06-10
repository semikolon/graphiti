# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Fork Branch Note**: This fork uses `feature/explicit-scoping` as de facto main (fork's `main` diverged 146 commits from upstream getzep/graphiti due to major MCP refactor). See `~/dotfiles/TODO.md` "Deferred: Graphiti Upstream Sync" for details.

Graphiti is a Python framework for building temporally-aware knowledge graphs designed for AI agents. It enables real-time incremental updates to knowledge graphs without batch recomputation, making it suitable for dynamic environments.

Key features:

- Bi-temporal data model with explicit tracking of event occurrence times
- Hybrid retrieval combining semantic embeddings, keyword search (BM25), and graph traversal
- Support for custom entity definitions via Pydantic models
- Integration with Neo4j and FalkorDB as graph storage backends

## Development Commands

### Main Development Commands (run from project root)

```bash
# Install dependencies
uv sync --extra dev

# Format code (ruff import sorting + formatting)
make format

# Lint code (ruff + pyright type checking)
make lint

# Run tests
make test

# Run all checks (format, lint, test)
make check
```

### Server Development (run from server/ directory)

```bash
cd server/
# Install server dependencies
uv sync --extra dev

# Run server in development mode
uvicorn graph_service.main:app --reload

# Format, lint, test server code
make format
make lint
make test
```

### MCP Server Development (run from mcp_server/ directory)

```bash
cd mcp_server/
# Install MCP server dependencies
uv sync

# Run with Docker Compose
docker-compose up
```

## Code Architecture

### Core Library (`graphiti_core/`)

- **Main Entry Point**: `graphiti.py` - Contains the main `Graphiti` class that orchestrates all functionality
- **Graph Storage**: `driver/` - Database drivers for Neo4j and FalkorDB
- **LLM Integration**: `llm_client/` - Clients for OpenAI, Anthropic, Gemini, Groq
- **Embeddings**: `embedder/` - Embedding clients for various providers
- **Graph Elements**: `nodes.py`, `edges.py` - Core graph data structures
- **Search**: `search/` - Hybrid search implementation with configurable strategies
- **Prompts**: `prompts/` - LLM prompts for entity extraction, deduplication, summarization
- **Utilities**: `utils/` - Maintenance operations, bulk processing, datetime handling

### Server (`server/`)

- **FastAPI Service**: `graph_service/main.py` - REST API server
- **Routers**: `routers/` - API endpoints for ingestion and retrieval
- **DTOs**: `dto/` - Data transfer objects for API contracts

### MCP Server (`mcp_server/`)

- **MCP Implementation**: `graphiti_mcp_server.py` - Model Context Protocol server for AI assistants
- **Docker Support**: Containerized deployment with Neo4j

## Testing

- **Unit Tests**: `tests/` - Comprehensive test suite using pytest
- **Integration Tests**: Tests marked with `_int` suffix require database connections
- **Evaluation**: `tests/evals/` - End-to-end evaluation scripts

## Configuration

### Environment Variables

- `OPENAI_API_KEY` - Required for LLM inference and embeddings
- `USE_PARALLEL_RUNTIME` - Optional boolean for Neo4j parallel runtime (enterprise only)
- Provider-specific keys: `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `GROQ_API_KEY`, `VOYAGE_API_KEY`

### Database Setup

- **Neo4j**: Version 5.26+ required, available via Neo4j Desktop
  - Database name defaults to `neo4j` (hardcoded in Neo4jDriver)
  - Override by passing `database` parameter to driver constructor
- **FalkorDB**: Version 1.1.2+ as alternative backend
  - Database name defaults to `default_db` (hardcoded in FalkorDriver)
  - Override by passing `database` parameter to driver constructor

## Development Guidelines

### Code Style

- Use Ruff for formatting and linting (configured in pyproject.toml)
- Line length: 100 characters
- Quote style: single quotes
- Type checking with Pyright is enforced
- Main project uses `typeCheckingMode = "basic"`, server uses `typeCheckingMode = "standard"`

### Testing Requirements

- Run tests with `make test` or `pytest`
- Integration tests require database connections and are marked with `_int` suffix
- Use `pytest-xdist` for parallel test execution
- Run specific test files: `pytest tests/test_specific_file.py`
- Run specific test methods: `pytest tests/test_file.py::test_method_name`
- Run only integration tests: `pytest tests/ -k "_int"`
- Run only unit tests: `pytest tests/ -k "not _int"`

### LLM Provider Support

The codebase supports multiple LLM providers but works best with services supporting structured output (OpenAI, Gemini). Other providers may cause schema validation issues, especially with smaller models.

### MCP Server Usage Guidelines

When working with the MCP server, follow the patterns established in `mcp_server/cursor_rules.md`:

- Always search for existing knowledge before adding new information
- Use specific entity type filters (`Preference`, `Procedure`, `Requirement`)
- Store new information immediately using `add_memory`
- Follow discovered procedures and respect established preferences

### Retrieval tiers — what the MCP exposes vs what graphiti-core ships (graph RAG)

Every `search_facts`/`search_nodes` call whose hits feed an LLM answer IS graph RAG (hybrid BM25 + vector + graph traversal augmenting generation) — the basic, single-shot tier. graphiti-core ships richer tiers we don't fully expose:

- **Node-distance reranking** — ALREADY exposed: `search_nodes`/`search_facts` accept `center_node_uuid`; pass it and results rerank by graph proximity (the `NODE_HYBRID_SEARCH_NODE_DISTANCE` recipe, `graphiti_mcp_server.py:1332`). Pattern: search → grab the entity uuid → re-search centered on it = associative "everything connected to X".
- **MMR reranker** (Maximal Marginal Relevance — relevance × diversity, kills near-duplicate facts) and **cross-encoder reranker** (joint query-result scoring, higher precision) — recipes exist in `graphiti_core/search/search_config_recipes.py` (`*_HYBRID_SEARCH_MMR` / `_CROSS_ENCODER`) but are NOT selectable from the MCP search tools (only RRF + NODE_DISTANCE are wired). Exposing a `reranker` param is a small addition. MMR's diversity uses cosine over the existing Qwen3 embeddings (Darwin :8080) — no new model; cross-encoder needs a separate reranker (OpenAI/Gemini/local BGE).
- **`build_communities`** (label-propagation, `graphiti_core/utils/maintenance/community_operations.py`) + community search recipes — NOT exposed as an MCP tool. The "themes across everything / overview" enabler.

**Sequencing (RESOLVED 2026-06-10):** BUILD reranker/community tools on the fork's 0.20.1 NOW. The MMR + cross-encoder recipes, the reranker clients, and `build_communities` (label-propagation) all already ship in the fork's `graphiti_core`; only MCP-server *exposure* is missing (hours, additive, near-zero conflict). Cross-encoder is even already live (`Graphiti` constructor auto-inits `OpenAIRerankerClient`; the daemon passes none). Do NOT gate on reintegration: upstream's MCP also wires only RRF + NODE_DISTANCE (reintegration wouldn't hand us MMR/cross-encoder for free), and a naive rebase would risk the fork's still-unmerged reliability patches (#1176 worker-GC, #1164 attr-collision — both OPEN upstream). Reintegration (0.20.1 → 0.29.x; 79 ahead / 237+ behind) is real but ORTHOGONAL debt → a separate, deliberately-scoped **rebuild-on-upstream** project (not a rebase); cherry-pick 0.29.1 attribute-hallucination guards + 0.28.2 Cypher-hardening opportunistically. HippoRAG/PPR is net-new (absent from both fork + upstream) → separate spike, lowest priority. Full analysis: `docs/upstream_divergence_and_reintegration_2026_06_10.md`. Reliability-patch provenance: `~/dotfiles/docs/graphiti_upstream_review_2026_03_15.md`. DIM consumer-side verdict: `~/Projects/din-mamma/docs/research/graphrag_landscape_second_pass_2026_06_09.md`.

### Custom Cypher Tools (fork additions)

The fork adds two Cypher-level tools beyond standard MCP — choose by intent, not convenience:

- **`raw_cypher_query`** (read-only, fenced): Arbitrary read traversal. Blocks ALL writes (CREATE/DELETE/SET/MERGE/REMOVE/DETACH/DROP). Use for graph exploration, complex MATCH patterns beyond `search_nodes`/`search_facts`.
- **`cypher_query_write`** (write-capable, May 5 2026, after `raw_cypher_query` at `mcp_server/graphiti_mcp_server.py:1972`): Allows CREATE/SET/MERGE/DELETE/REMOVE/DETACH. **Blocks DROP only** (case-insensitive, word-boundary regex). Group-scoped via `driver.clone(database=group_id)`. Auto-adds LIMIT to RETURN clauses. Audit-logged via `logger.info`. 19 tests in `test_cypher_query_write.py`. Use for **structured CRUD on known schema** where LLM extraction is overhead — direct entity create/update/delete by callers that already know the shape. **`max_results` cap: 10000** (raised from 500 in commit `2c14aa0`, May 5 2026, after Fyr's master-todo-system listTasks-for-diff path silently truncated → duplicate task creation on every rescan beyond the cap; `raw_cypher_query` stays at 500 since it's read-only and used for ad-hoc exploration). For workloads > 10K rows, switch to SKIP/LIMIT pagination in the client. **FastMCP response gotcha**: the `list[dict] | ErrorResponse` return is serialized as `structuredContent: {result: [...]}` envelope AND `content: [{text:row1}, {text:row2}, ...]` (one text item per row, NOT a single JSON array). Clients must unwrap the `{result: ...}` envelope and iterate all content items — see `~/Projects/fyr/sidecar/src/graphiti-client.ts::parseToolResult` for the canonical parsing pattern.

### When to use `add_memory` vs `cypher_query_write`

- **`add_memory`** — natural-language episode capture where LLM entity extraction earns its cost. Goes through full pipeline: extraction → reflexion → dedup → edge resolution → attribute extraction → bulk write. ~5-7 minutes per episode on populated graphs (per global CLAUDE.md § Knowledge Infrastructure Reliability). Right for free-form conversations, ambient ingestion, anywhere you want the graph to get richer through the LLM's interpretation.
- **`cypher_query_write`** — structured CRUD on known schema. Skips LLM extraction entirely. Right for systems that already know the entity shape (e.g., Fyr's `master-todo-system` doing Task CRUD via the new tool — see `~/Projects/fyr/.claude/specs/master-todo-system/design.md`). Sub-second latency; same per-episode entity_edges count would otherwise pay the full LLM-pipeline cost.

### Custom Entity Schema Updates

- **Task** (May 5 2026, commit `2758264`): Gained `external_source` + `external_id` Optional fields after `context`. Carries provenance for tasks scanned from external systems (TickTick/Workflowy/Keep/todomd) so re-scans can re-find the same task across runs. 7 Pydantic round-trip tests verify schema. Source: `mcp_server/custom_entities.py:164-220`.