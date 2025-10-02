# Graphiti MCP Server

Graphiti is a framework for building and querying temporally-aware knowledge graphs, specifically tailored for AI agents
operating in dynamic environments. Unlike traditional retrieval-augmented generation (RAG) methods, Graphiti
continuously integrates user interactions, structured and unstructured enterprise data, and external information into a
coherent, queryable graph. The framework supports incremental data updates, efficient retrieval, and precise historical
queries without requiring complete graph recomputation, making it suitable for developing interactive, context-aware AI
applications.

This is an experimental Model Context Protocol (MCP) server implementation for Graphiti. The MCP server exposes
Graphiti's key functionality through the MCP protocol, allowing AI assistants to interact with Graphiti's knowledge
graph capabilities.

## Features

The Graphiti MCP server exposes the following key high-level functions of Graphiti:

- **Episode Management**: Add, retrieve, and delete episodes (text, messages, or JSON data)
- **Entity Management**: Search and manage entity nodes and relationships in the knowledge graph
- **Search Capabilities**: Search for facts (edges) and node summaries using semantic and hybrid search
- **Group Management**: Organize and manage groups of related data with group_id filtering
- **Graph Maintenance**: Clear the graph and rebuild indices
- **Explicit Scoping**: Three-tier scope system (project/global/cross-project) preventing accidental data leaks
- **Custom Entities**: Supports ProgrammingLanguage, Project, HomeAssistantDevice entity types for structured ontology
- **FalkorDB Backend**: 69-254x faster than Neo4j with sub-10ms query latency

## Explicit Scoping

Graphiti MCP implements **Explicit Scoping** to prevent accidental data leaks between projects. Scope is encoded in tool names, not runtime parameters, making LLM intent explicit and safe.

### Why Explicit Scoping?

**Problem**: Previous versions allowed LLMs to override `group_id` parameters, enabling accidental cross-project data contamination despite careful wrapper-based scoping.

**Solution**: Three scope levels with distinct tool names, where scope is explicit and cannot be overridden.

### Tool Scopes

#### Project-Scoped (Default)
Uses current project's memory graph only. Safe for normal development.

**Tools**:
- `add_memory(name, body)` - Add episode to current project
- `search_nodes(query)` - Search nodes in current project
- `search_facts(query)` - Search facts in current project

**Scoping**: Uses `group_id` from config (set by shims wrapper based on git project). Cannot leak data to other projects.

**Example**:
```python
# In dotfiles project (group_id="dotfiles")
add_memory(
    name="Configuration Update",
    episode_body="Updated zshrc with new aliases"
)
# Data goes to "dotfiles" group only
```

#### Global-Scoped (Explicit)
Uses shared "default" memory graph. For cross-project general knowledge.

**Tools**:
- `add_global_memory(name, body)` - Add to global graph
- `search_global_nodes(query)` - Search global nodes
- `search_global_facts(query)` - Search global facts

**Scoping**: Hardcoded to `'default'` group. Intent is explicit in tool name.

**Example**:
```python
# Any project context
add_global_memory(
    name="Python Best Practices",
    episode_body="Always use type hints and docstrings"
)
# Data goes to shared "default" group
```

#### Cross-Project (Explicit, Intentional)
Explicit list of projects for pattern comparison. Rare use case.

**Tools**:
- `search_cross_project_nodes(query, projects)` - Search across projects
- `search_cross_project_facts(query, projects)` - Search facts across projects

**Scoping**: Requires explicit `projects` list. Fails if empty.

**Example**:
```python
# Compare authentication patterns across projects
search_cross_project_nodes(
    query="authentication implementation",
    projects=["dotfiles", "kimonokittens", "brf-auto"]
)
```

**Limit Behavior**: `max_nodes`/`max_facts` applies **globally** across all projects.

- Querying 3 projects with `max_nodes=10` returns **10 total results**, not 30
- For balanced representation, use proportionally higher limits (e.g., `max_nodes=30` for 3 projects)
- Limit is applied after merging results from all specified group_ids

**Technical**: Graphiti sets `search_config.limit = num_results` once, then queries all group_ids. FalkorDB executes: `WHERE origin.group_id IN $group_ids LIMIT $limit`

### Trust Model Guarantees

✅ **Project Isolation**: Default tools cannot access other projects
✅ **Explicit Intent**: Global/cross-project intent is obvious in tool name
✅ **Parameter Safety**: No `group_id` parameters can bypass scoping
✅ **Shims Integration**: Works seamlessly with wrapper-based GROUP_ID management

### Performance

**Database**: FalkorDB backend provides 69-254x performance improvement over Neo4j

**Cross-Project Queries**:
- Single project: Sub-10ms p99 latency
- Multiple projects: Linear scaling expected
- Recommended: Up to 10 projects for interactive queries

**Real-world validation ongoing** - performance characteristics will be updated based on production usage.

### Migration from Old Tools

Old tools (with `group_id` parameters) have been replaced:

| Old Tool | New Project-Scoped | New Global-Scoped | New Cross-Project | Why Changed |
|----------|-------------------|-------------------|-------------------|-------------|
| `add_memory(group_id=?)` | `add_memory()` | `add_global_memory()` | N/A | Prevent accidental cross-project data leaks via LLM parameter override |
| `search_memory_nodes(group_ids=?)` | `search_nodes()` | `search_global_nodes()` | `search_cross_project_nodes(projects)` | Make cross-project intent explicit in tool name, not hidden in parameters |
| `search_memory_facts(group_ids=?)` | `search_facts()` | `search_global_facts()` | `search_cross_project_facts(projects)` | Enforce trust model through API design, not runtime validation |

### Testing

Run explicit scoping unit tests:
```bash
pytest test_explicit_scoping.py -v
```

Tests verify:
- Project-scoped tools use `config.group_id` only
- Global-scoped tools use hardcoded `'default'` group
- Cross-project tools require explicit project list
- LLMs cannot bypass project isolation via parameters

## Quick Start

### Clone the Graphiti GitHub repo

```bash
git clone https://github.com/getzep/graphiti.git
```

or

```bash
gh repo clone getzep/graphiti
```

### For Claude Desktop and other `stdio` only clients

1. Note the full path to this directory.

```
cd graphiti && pwd
```

2. Install the [Graphiti prerequisites](#prerequisites).

3. Configure Claude, Cursor, or other MCP client to use [Graphiti with a `stdio` transport](#integrating-with-mcp-clients). See the client documentation on where to find their MCP configuration files.

### For Cursor and other `sse`-enabled clients

1. Change directory to the `mcp_server` directory

`cd graphiti/mcp_server`

2. Start the service using Docker Compose

`docker compose up`

3. Point your MCP client to `http://localhost:8000/sse`

## Installation

### Prerequisites

1. Ensure you have Python 3.10 or higher installed.
2. A running Neo4j database (version 5.26 or later required)
3. OpenAI API key for LLM operations

### Setup

1. Clone the repository and navigate to the mcp_server directory
2. Use `uv` to create a virtual environment and install dependencies:

```bash
# Install uv if you don't have it already
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create a virtual environment and install dependencies in one step
uv sync
```

## Configuration

The server uses the following environment variables:

- `NEO4J_URI`: URI for the Neo4j database (default: `bolt://localhost:7687`)
- `NEO4J_USER`: Neo4j username (default: `neo4j`)
- `NEO4J_PASSWORD`: Neo4j password (default: `demodemo`)
- `OPENAI_API_KEY`: OpenAI API key (required for LLM operations)
- `OPENAI_BASE_URL`: Optional base URL for OpenAI API
- `MODEL_NAME`: OpenAI model name to use for LLM operations.
- `SMALL_MODEL_NAME`: OpenAI model name to use for smaller LLM operations.
- `LLM_TEMPERATURE`: Temperature for LLM responses (0.0-2.0).
- `AZURE_OPENAI_ENDPOINT`: Optional Azure OpenAI LLM endpoint URL
- `AZURE_OPENAI_DEPLOYMENT_NAME`: Optional Azure OpenAI LLM deployment name
- `AZURE_OPENAI_API_VERSION`: Optional Azure OpenAI LLM API version
- `AZURE_OPENAI_EMBEDDING_API_KEY`: Optional Azure OpenAI Embedding deployment key (if other than `OPENAI_API_KEY`)
- `AZURE_OPENAI_EMBEDDING_ENDPOINT`: Optional Azure OpenAI Embedding endpoint URL
- `AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME`: Optional Azure OpenAI embedding deployment name
- `AZURE_OPENAI_EMBEDDING_API_VERSION`: Optional Azure OpenAI API version
- `AZURE_OPENAI_USE_MANAGED_IDENTITY`: Optional use Azure Managed Identities for authentication
- `SEMAPHORE_LIMIT`: Episode processing concurrency. See [Concurrency and LLM Provider 429 Rate Limit Errors](#concurrency-and-llm-provider-429-rate-limit-errors)

You can set these variables in a `.env` file in the project directory.

## Running the Server

To run the Graphiti MCP server directly using `uv`:

```bash
uv run graphiti_mcp_server.py
```

With options:

```bash
uv run graphiti_mcp_server.py --model gpt-4.1-mini --transport sse
```

Available arguments:

- `--model`: Overrides the `MODEL_NAME` environment variable.
- `--small-model`: Overrides the `SMALL_MODEL_NAME` environment variable.
- `--temperature`: Overrides the `LLM_TEMPERATURE` environment variable.
- `--transport`: Choose the transport method (sse or stdio, default: sse)
- `--group-id`: Set a namespace for the graph (optional). If not provided, defaults to "default".
- `--destroy-graph`: If set, destroys all Graphiti graphs on startup.
- `--use-custom-entities`: Enable entity extraction using the predefined ENTITY_TYPES

### Concurrency and LLM Provider 429 Rate Limit Errors

Graphiti's ingestion pipelines are designed for high concurrency, controlled by the `SEMAPHORE_LIMIT` environment variable.
By default, `SEMAPHORE_LIMIT` is set to `10` concurrent operations to help prevent `429` rate limit errors from your LLM provider. If you encounter such errors, try lowering this value.

If your LLM provider allows higher throughput, you can increase `SEMAPHORE_LIMIT` to boost episode ingestion performance.

### Docker Deployment

The Graphiti MCP server can be deployed using Docker. The Dockerfile uses `uv` for package management, ensuring
consistent dependency installation.

#### Environment Configuration

Before running the Docker Compose setup, you need to configure the environment variables. You have two options:

1. **Using a .env file** (recommended):

   - Copy the provided `.env.example` file to create a `.env` file:
     ```bash
     cp .env.example .env
     ```
   - Edit the `.env` file to set your OpenAI API key and other configuration options:
     ```
     # Required for LLM operations
     OPENAI_API_KEY=your_openai_api_key_here
     MODEL_NAME=gpt-4.1-mini
     # Optional: OPENAI_BASE_URL only needed for non-standard OpenAI endpoints
     # OPENAI_BASE_URL=https://api.openai.com/v1
     ```
   - The Docker Compose setup is configured to use this file if it exists (it's optional)

2. **Using environment variables directly**:
   - You can also set the environment variables when running the Docker Compose command:
     ```bash
     OPENAI_API_KEY=your_key MODEL_NAME=gpt-4.1-mini docker compose up
     ```

#### Neo4j Configuration

The Docker Compose setup includes a Neo4j container with the following default configuration:

- Username: `neo4j`
- Password: `demodemo`
- URI: `bolt://neo4j:7687` (from within the Docker network)
- Memory settings optimized for development use

#### Running with Docker Compose

A Graphiti MCP container is available at: `zepai/knowledge-graph-mcp`. The latest build of this container is used by the Compose setup below.

Start the services using Docker Compose:

```bash
docker compose up
```

Or if you're using an older version of Docker Compose:

```bash
docker-compose up
```

This will start both the Neo4j database and the Graphiti MCP server. The Docker setup:

- Uses `uv` for package management and running the server
- Installs dependencies from the `pyproject.toml` file
- Connects to the Neo4j container using the environment variables
- Exposes the server on port 8000 for HTTP-based SSE transport
- Includes a healthcheck for Neo4j to ensure it's fully operational before starting the MCP server

## Integrating with MCP Clients

### Configuration

To use the Graphiti MCP server with an MCP-compatible client, configure it to connect to the server:

> [!IMPORTANT]
> You will need the Python package manager, `uv` installed. Please refer to the [`uv` install instructions](https://docs.astral.sh/uv/getting-started/installation/).
>
> Ensure that you set the full path to the `uv` binary and your Graphiti project folder.

```json
{
  "mcpServers": {
    "graphiti-memory": {
      "transport": "stdio",
      "command": "/Users/<user>/.local/bin/uv",
      "args": [
        "run",
        "--isolated",
        "--directory",
        "/Users/<user>>/dev/zep/graphiti/mcp_server",
        "--project",
        ".",
        "graphiti_mcp_server.py",
        "--transport",
        "stdio"
      ],
      "env": {
        "NEO4J_URI": "bolt://localhost:7687",
        "NEO4J_USER": "neo4j",
        "NEO4J_PASSWORD": "password",
        "OPENAI_API_KEY": "sk-XXXXXXXX",
        "MODEL_NAME": "gpt-4.1-mini"
      }
    }
  }
}
```

For SSE transport (HTTP-based), you can use this configuration:

```json
{
  "mcpServers": {
    "graphiti-memory": {
      "transport": "sse",
      "url": "http://localhost:8000/sse"
    }
  }
}
```

## Available Tools

The Graphiti MCP server exposes the following tools:

- `add_episode`: Add an episode to the knowledge graph (supports text, JSON, and message formats)
- `search_nodes`: Search the knowledge graph for relevant node summaries
- `search_facts`: Search the knowledge graph for relevant facts (edges between entities)
- `delete_entity_edge`: Delete an entity edge from the knowledge graph
- `delete_episode`: Delete an episode from the knowledge graph
- `get_entity_edge`: Get an entity edge by its UUID
- `get_episodes`: Get the most recent episodes for a specific group
- `clear_graph`: Clear all data from the knowledge graph and rebuild indices
- `get_status`: Get the status of the Graphiti MCP server and Neo4j connection

## Working with JSON Data

The Graphiti MCP server can process structured JSON data through the `add_episode` tool with `source="json"`. This
allows you to automatically extract entities and relationships from structured data:

```

add_episode(
name="Customer Profile",
episode_body="{\"company\": {\"name\": \"Acme Technologies\"}, \"products\": [{\"id\": \"P001\", \"name\": \"CloudSync\"}, {\"id\": \"P002\", \"name\": \"DataMiner\"}]}",
source="json",
source_description="CRM data"
)

```

## Integrating with the Cursor IDE

To integrate the Graphiti MCP Server with the Cursor IDE, follow these steps:

1. Run the Graphiti MCP server using the SSE transport:

```bash
python graphiti_mcp_server.py --transport sse --use-custom-entities --group-id <your_group_id>
```

Hint: specify a `group_id` to namespace graph data. If you do not specify a `group_id`, the server will use "default" as the group_id.

or

```bash
docker compose up
```

2. Configure Cursor to connect to the Graphiti MCP server.

```json
{
  "mcpServers": {
    "graphiti-memory": {
      "url": "http://localhost:8000/sse"
    }
  }
}
```

3. Add the Graphiti rules to Cursor's User Rules. See [cursor_rules.md](cursor_rules.md) for details.

4. Kick off an agent session in Cursor.

The integration enables AI assistants in Cursor to maintain persistent memory through Graphiti's knowledge graph
capabilities.

## Integrating with Claude Desktop (Docker MCP Server)

The Graphiti MCP Server container uses the SSE MCP transport. Claude Desktop does not natively support SSE, so you'll need to use a gateway like `mcp-remote`.

1.  **Run the Graphiti MCP server using SSE transport**:

    ```bash
    docker compose up
    ```

2.  **(Optional) Install `mcp-remote` globally**:
    If you prefer to have `mcp-remote` installed globally, or if you encounter issues with `npx` fetching the package, you can install it globally. Otherwise, `npx` (used in the next step) will handle it for you.

    ```bash
    npm install -g mcp-remote
    ```

3.  **Configure Claude Desktop**:
    Open your Claude Desktop configuration file (usually `claude_desktop_config.json`) and add or modify the `mcpServers` section as follows:

    ```json
    {
      "mcpServers": {
        "graphiti-memory": {
          // You can choose a different name if you prefer
          "command": "npx", // Or the full path to mcp-remote if npx is not in your PATH
          "args": [
            "mcp-remote",
            "http://localhost:8000/sse" // Ensure this matches your Graphiti server's SSE endpoint
          ]
        }
      }
    }
    ```

    If you already have an `mcpServers` entry, add `graphiti-memory` (or your chosen name) as a new key within it.

4.  **Restart Claude Desktop** for the changes to take effect.

## Requirements

- Python 3.10 or higher
- Neo4j database (version 5.26 or later required)
- OpenAI API key (for LLM operations and embeddings)
- MCP-compatible client

## Telemetry

The Graphiti MCP server uses the Graphiti core library, which includes anonymous telemetry collection. When you initialize the Graphiti MCP server, anonymous usage statistics are collected to help improve the framework.

### What's Collected

- Anonymous identifier and system information (OS, Python version)
- Graphiti version and configuration choices (LLM provider, database backend, embedder type)
- **No personal data, API keys, or actual graph content is ever collected**

### How to Disable

To disable telemetry in the MCP server, set the environment variable:

```bash
export GRAPHITI_TELEMETRY_ENABLED=false
```

Or add it to your `.env` file:

```
GRAPHITI_TELEMETRY_ENABLED=false
```

For complete details about what's collected and why, see the [Telemetry section in the main Graphiti README](../README.md#telemetry).

## License

This project is licensed under the same license as the parent Graphiti project.
