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

### Custom Cypher Tools (fork additions)

The fork adds two Cypher-level tools beyond standard MCP — choose by intent, not convenience:

- **`raw_cypher_query`** (read-only, fenced): Arbitrary read traversal. Blocks ALL writes (CREATE/DELETE/SET/MERGE/REMOVE/DETACH/DROP). Use for graph exploration, complex MATCH patterns beyond `search_nodes`/`search_facts`.
- **`cypher_query_write`** (write-capable, May 5 2026, after `raw_cypher_query` at `mcp_server/graphiti_mcp_server.py:1972`): Allows CREATE/SET/MERGE/DELETE/REMOVE/DETACH. **Blocks DROP only** (case-insensitive, word-boundary regex). Group-scoped via `driver.clone(database=group_id)`. Auto-adds LIMIT to RETURN clauses. Audit-logged via `logger.info`. 19 tests in `test_cypher_query_write.py`. Use for **structured CRUD on known schema** where LLM extraction is overhead — direct entity create/update/delete by callers that already know the shape.

### When to use `add_memory` vs `cypher_query_write`

- **`add_memory`** — natural-language episode capture where LLM entity extraction earns its cost. Goes through full pipeline: extraction → reflexion → dedup → edge resolution → attribute extraction → bulk write. ~5-7 minutes per episode on populated graphs (per global CLAUDE.md § Knowledge Infrastructure Reliability). Right for free-form conversations, ambient ingestion, anywhere you want the graph to get richer through the LLM's interpretation.
- **`cypher_query_write`** — structured CRUD on known schema. Skips LLM extraction entirely. Right for systems that already know the entity shape (e.g., Fyr's `master-todo-system` doing Task CRUD via the new tool — see `~/Projects/fyr/.claude/specs/master-todo-system/design.md`). Sub-second latency; same per-episode entity_edges count would otherwise pay the full LLM-pipeline cost.

### Custom Entity Schema Updates

- **Task** (May 5 2026, commit `2758264`): Gained `external_source` + `external_id` Optional fields after `context`. Carries provenance for tasks scanned from external systems (TickTick/Workflowy/Keep/todomd) so re-scans can re-find the same task across runs. 7 Pydantic round-trip tests verify schema. Source: `mcp_server/custom_entities.py:164-220`.