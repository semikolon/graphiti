#!/usr/bin/env python3
"""
Graphiti MCP Server - Exposes Graphiti functionality through the Model Context Protocol (MCP)
"""

import argparse
import asyncio
import logging
import logging.handlers
import os
import re
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from typing_extensions import TypedDict

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from dotenv import load_dotenv
from mcp.server.fastmcp import Context, FastMCP
from openai import AsyncAzureOpenAI
from pydantic import BaseModel, Field

from custom_entities import (
    BusinessRule,
    Commitment,
    Deadline,
    Decision,
    ExternalConstraint,
    Framework,
    Library,
    Meeting,
    Pattern,
    Person,
    PolicyDecision,
    ProgrammingLanguage,
    Project,
    Routine,
    ServiceAPI,
    Task,
    Topic,
    WorkflowChoice,
)
from notifications import (
    classify_processing_error,
    get_recent_errors_list,
    notify_episode_failure,
    record_error,
)
from graphiti_core import Graphiti
from graphiti_core.driver.falkordb_driver import FalkorDriver
from graphiti_core.edges import EntityEdge
from graphiti_core.embedder.azure_openai import AzureOpenAIEmbedderClient
from graphiti_core.embedder.client import EmbedderClient
from graphiti_core.embedder.fallback import FallbackEmbedder
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.llm_client import LLMClient
from graphiti_core.llm_client.azure_openai_client import AzureOpenAILLMClient
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.openai_client import OpenAIClient
from graphiti_core.nodes import EpisodeType, EpisodicNode
from graphiti_core.search.search_config_recipes import (
    NODE_HYBRID_SEARCH_NODE_DISTANCE,
    NODE_HYBRID_SEARCH_RRF,
)
from graphiti_core.search.search_filters import SearchFilters
from graphiti_core.utils.maintenance.graph_data_operations import build_indices_and_constraints, clear_data

load_dotenv()

# FalkorDBLite support - embedded FalkorDB without Docker.
# load_dotenv() must happen before we resolve USE_FALKORDBLITE so direct launches
# respect the .env file without requiring an outer shell wrapper to source it.
USE_FALKORDBLITE = os.environ.get('USE_FALKORDBLITE', '').lower() in ('1', 'true', 'yes')
if USE_FALKORDBLITE:
    from redislite import AsyncFalkorDB as EmbeddedAsyncFalkorDB


DEFAULT_LLM_MODEL = 'gpt-5-mini'
SMALL_LLM_MODEL = 'gpt-5-nano'
DEFAULT_EMBEDDER_MODEL = 'text-embedding-3-small'

# Semaphore limit for concurrent Graphiti operations.
# Decrease this if you're experiencing 429 rate limit errors from your LLM provider.
# Increase if you have high rate limits.
SEMAPHORE_LIMIT = int(os.getenv('SEMAPHORE_LIMIT', 10))


class Requirement(BaseModel):
    """A Requirement represents a specific need, feature, or functionality that a product or service must fulfill.

    Always ensure an edge is created between the requirement and the project it belongs to, and clearly indicate on the
    edge that the requirement is a requirement.

    Instructions for identifying and extracting requirements:
    1. Look for explicit statements of needs or necessities ("We need X", "X is required", "X must have Y")
    2. Identify functional specifications that describe what the system should do
    3. Pay attention to non-functional requirements like performance, security, or usability criteria
    4. Extract constraints or limitations that must be adhered to
    5. Focus on clear, specific, and measurable requirements rather than vague wishes
    6. Capture the priority or importance if mentioned ("critical", "high priority", etc.)
    7. Include any dependencies between requirements when explicitly stated
    8. Preserve the original intent and scope of the requirement
    9. Categorize requirements appropriately based on their domain or function
    """

    project_name: str = Field(
        ...,
        description='The name of the project to which the requirement belongs.',
    )
    description: str = Field(
        ...,
        description='Description of the requirement. Only use information mentioned in the context to write this description.',
    )


class Preference(BaseModel):
    """A Preference represents a user's expressed like, dislike, or preference for something.

    Instructions for identifying and extracting preferences:
    1. Look for explicit statements of preference such as "I like/love/enjoy/prefer X" or "I don't like/hate/dislike X"
    2. Pay attention to comparative statements ("I prefer X over Y")
    3. Consider the emotional tone when users mention certain topics
    4. Extract only preferences that are clearly expressed, not assumptions
    5. Categorize the preference appropriately based on its domain (food, music, brands, etc.)
    6. Include relevant qualifiers (e.g., "likes spicy food" rather than just "likes food")
    7. Only extract preferences directly stated by the user, not preferences of others they mention
    8. Provide a concise but specific description that captures the nature of the preference
    """

    category: str = Field(
        ...,
        description="The category of the preference. (e.g., 'Brands', 'Food', 'Music')",
    )
    description: str = Field(
        ...,
        description='Brief description of the preference. Only use information mentioned in the context to write this description.',
    )


class Procedure(BaseModel):
    """A Procedure informing the agent what actions to take or how to perform in certain scenarios. Procedures are typically composed of several steps.

    Instructions for identifying and extracting procedures:
    1. Look for sequential instructions or steps ("First do X, then do Y")
    2. Identify explicit directives or commands ("Always do X when Y happens")
    3. Pay attention to conditional statements ("If X occurs, then do Y")
    4. Extract procedures that have clear beginning and end points
    5. Focus on actionable instructions rather than general information
    6. Preserve the original sequence and dependencies between steps
    7. Include any specified conditions or triggers for the procedure
    8. Capture any stated purpose or goal of the procedure
    9. Summarize complex procedures while maintaining critical details
    """

    description: str = Field(
        ...,
        description='Brief description of the procedure. Only use information mentioned in the context to write this description.',
    )


ENTITY_TYPES: dict[str, BaseModel] = {
    'Requirement': Requirement,  # type: ignore
    'Preference': Preference,  # type: ignore
    'Procedure': Procedure,  # type: ignore
    'ProgrammingLanguage': ProgrammingLanguage,  # type: ignore
    'Project': Project,  # type: ignore
    'Library': Library,  # type: ignore
    'Framework': Framework,  # type: ignore
    'ServiceAPI': ServiceAPI,  # type: ignore
    'Pattern': Pattern,  # type: ignore
    'Topic': Topic,  # type: ignore
    # Decision types (all have decision_id for triple-storage, except ExternalConstraint)
    'Decision': Decision,  # type: ignore  # Technical/architectural decisions
    'BusinessRule': BusinessRule,  # type: ignore  # Domain-specific rules/formulas/thresholds
    'PolicyDecision': PolicyDecision,  # type: ignore  # Organizational/strategic/brand decisions
    'WorkflowChoice': WorkflowChoice,  # type: ignore  # Process/ceremony/workflow decisions
    'ExternalConstraint': ExternalConstraint,  # type: ignore  # Regulations/laws (not our decision)
    # Task management entities (Fyr personal assistant)
    'Task': Task,  # type: ignore  # Actionable items with status/priority/deps
    'Deadline': Deadline,  # type: ignore  # Temporal constraints (shared deadlines)
    'Routine': Routine,  # type: ignore  # Recurring patterns that generate Tasks
    # Household / interpersonal entities (Ruby household context)
    'Person': Person,  # type: ignore  # Humans known to the household
    'Commitment': Commitment,  # type: ignore  # Interpersonal promises (distinct from Task)
    'Meeting': Meeting,  # type: ignore  # Bounded conversation episodes (instance, not pattern)
}


# Type definitions for API responses
class ErrorResponse(TypedDict):
    error: str


class SuccessResponse(TypedDict):
    message: str


class NodeResult(TypedDict):
    uuid: str
    name: str
    summary: str
    labels: list[str]
    group_id: str
    created_at: str
    attributes: dict[str, Any]


class NodeSearchResponse(TypedDict):
    message: str
    nodes: list[NodeResult]


class FactSearchResponse(TypedDict):
    message: str
    facts: list[dict[str, Any]]


class EpisodeSearchResponse(TypedDict):
    message: str
    episodes: list[dict[str, Any]]


class StatusResponse(TypedDict):
    status: str
    message: str


def create_azure_credential_token_provider() -> Callable[[], str]:
    credential = DefaultAzureCredential()
    token_provider = get_bearer_token_provider(
        credential, 'https://cognitiveservices.azure.com/.default'
    )
    return token_provider


# Server configuration classes
# The configuration system has a hierarchy:
# - GraphitiConfig is the top-level configuration
#   - LLMConfig handles all OpenAI/LLM related settings
#   - EmbedderConfig manages embedding settings
#   - Neo4jConfig manages database connection details
#   - Various other settings like group_id and feature flags
# Configuration values are loaded from:
# 1. Default values in the class definitions
# 2. Environment variables (loaded via load_dotenv())
# 3. Command line arguments (which override environment variables)
class GraphitiLLMConfig(BaseModel):
    """Configuration for the LLM client.

    Centralizes all LLM-specific configuration parameters including API keys and model selection.
    """

    api_key: str | None = None
    model: str = DEFAULT_LLM_MODEL
    small_model: str = SMALL_LLM_MODEL
    temperature: float = 0.0
    azure_openai_endpoint: str | None = None
    azure_openai_deployment_name: str | None = None
    azure_openai_api_version: str | None = None
    azure_openai_use_managed_identity: bool = False

    @classmethod
    def from_env(cls) -> 'GraphitiLLMConfig':
        """Create LLM configuration from environment variables."""
        # Get model from environment, or use default if not set or empty
        model_env = os.environ.get('MODEL_NAME', '')
        model = model_env if model_env.strip() else DEFAULT_LLM_MODEL

        # Get small_model from environment, or use default if not set or empty
        small_model_env = os.environ.get('SMALL_MODEL_NAME', '')
        small_model = small_model_env if small_model_env.strip() else SMALL_LLM_MODEL

        azure_openai_endpoint = os.environ.get('AZURE_OPENAI_ENDPOINT', None)
        azure_openai_api_version = os.environ.get('AZURE_OPENAI_API_VERSION', None)
        azure_openai_deployment_name = os.environ.get('AZURE_OPENAI_DEPLOYMENT_NAME', None)
        azure_openai_use_managed_identity = (
            os.environ.get('AZURE_OPENAI_USE_MANAGED_IDENTITY', 'false').lower() == 'true'
        )

        if azure_openai_endpoint is None:
            # Setup for OpenAI API
            # Log if empty model was provided
            if model_env == '':
                logger.debug(
                    f'MODEL_NAME environment variable not set, using default: {DEFAULT_LLM_MODEL}'
                )
            elif not model_env.strip():
                logger.warning(
                    f'Empty MODEL_NAME environment variable, using default: {DEFAULT_LLM_MODEL}'
                )

            return cls(
                api_key=os.environ.get('OPENAI_API_KEY'),
                model=model,
                small_model=small_model,
                temperature=float(os.environ.get('LLM_TEMPERATURE', '0.0')),
            )
        else:
            # Setup for Azure OpenAI API
            # Log if empty deployment name was provided
            if azure_openai_deployment_name is None:
                logger.error('AZURE_OPENAI_DEPLOYMENT_NAME environment variable not set')

                raise ValueError('AZURE_OPENAI_DEPLOYMENT_NAME environment variable not set')
            if not azure_openai_use_managed_identity:
                # api key
                api_key = os.environ.get('OPENAI_API_KEY', None)
            else:
                # Managed identity
                api_key = None

            return cls(
                azure_openai_use_managed_identity=azure_openai_use_managed_identity,
                azure_openai_endpoint=azure_openai_endpoint,
                api_key=api_key,
                azure_openai_api_version=azure_openai_api_version,
                azure_openai_deployment_name=azure_openai_deployment_name,
                model=model,
                small_model=small_model,
                temperature=float(os.environ.get('LLM_TEMPERATURE', '0.0')),
            )

    @classmethod
    def from_cli_and_env(cls, args: argparse.Namespace) -> 'GraphitiLLMConfig':
        """Create LLM configuration from CLI arguments, falling back to environment variables."""
        # Start with environment-based config
        config = cls.from_env()

        # CLI arguments override environment variables when provided
        if hasattr(args, 'model') and args.model:
            # Only use CLI model if it's not empty
            if args.model.strip():
                config.model = args.model
            else:
                # Log that empty model was provided and default is used
                logger.warning(f'Empty model name provided, using default: {DEFAULT_LLM_MODEL}')

        if hasattr(args, 'small_model') and args.small_model:
            if args.small_model.strip():
                config.small_model = args.small_model
            else:
                logger.warning(f'Empty small_model name provided, using default: {SMALL_LLM_MODEL}')

        if hasattr(args, 'temperature') and args.temperature is not None:
            config.temperature = args.temperature

        return config

    def create_client(self) -> LLMClient:
        """Create an LLM client based on this configuration.

        Returns:
            LLMClient instance
        """

        if self.azure_openai_endpoint is not None:
            # Azure OpenAI API setup
            if self.azure_openai_use_managed_identity:
                # Use managed identity for authentication
                token_provider = create_azure_credential_token_provider()
                return AzureOpenAILLMClient(
                    azure_client=AsyncAzureOpenAI(
                        azure_endpoint=self.azure_openai_endpoint,
                        azure_deployment=self.azure_openai_deployment_name,
                        api_version=self.azure_openai_api_version,
                        azure_ad_token_provider=token_provider,
                    ),
                    config=LLMConfig(
                        api_key=self.api_key,
                        model=self.model,
                        small_model=self.small_model,
                        temperature=self.temperature,
                    ),
                )
            elif self.api_key:
                # Use API key for authentication
                return AzureOpenAILLMClient(
                    azure_client=AsyncAzureOpenAI(
                        azure_endpoint=self.azure_openai_endpoint,
                        azure_deployment=self.azure_openai_deployment_name,
                        api_version=self.azure_openai_api_version,
                        api_key=self.api_key,
                    ),
                    config=LLMConfig(
                        api_key=self.api_key,
                        model=self.model,
                        small_model=self.small_model,
                        temperature=self.temperature,
                    ),
                )
            else:
                raise ValueError('OPENAI_API_KEY must be set when using Azure OpenAI API')

        if not self.api_key:
            raise ValueError('OPENAI_API_KEY must be set when using OpenAI API')

        llm_client_config = LLMConfig(
            api_key=self.api_key, model=self.model, small_model=self.small_model
        )

        # Set temperature
        llm_client_config.temperature = self.temperature

        # Configure reasoning='high' for full GPT-5/5.2 models.
        # Note: mini/nano/codex models ignore this - openai_client.py skips unsupported params.
        return OpenAIClient(config=llm_client_config, reasoning='high')


class GraphitiEmbedderConfig(BaseModel):
    """Configuration for the embedder client.

    Centralizes all embedding-related configuration parameters.
    Supports local embedding servers (llama.cpp, TEI) via EMBEDDER_BASE_URL
    with automatic fallback to OpenAI API when the local server is unreachable.
    """

    model: str = DEFAULT_EMBEDDER_MODEL
    api_key: str | None = None
    base_url: str | None = None
    azure_openai_endpoint: str | None = None
    azure_openai_deployment_name: str | None = None
    azure_openai_api_version: str | None = None
    azure_openai_use_managed_identity: bool = False

    @classmethod
    def from_env(cls) -> 'GraphitiEmbedderConfig':
        """Create embedder configuration from environment variables."""

        # Get model from environment, or use default if not set or empty
        model_env = os.environ.get('EMBEDDER_MODEL_NAME', '')
        model = model_env if model_env.strip() else DEFAULT_EMBEDDER_MODEL

        # Local embedding server (llama.cpp, TEI, etc.)
        base_url = os.environ.get('EMBEDDER_BASE_URL', None)
        if base_url and not base_url.strip():
            base_url = None

        azure_openai_endpoint = os.environ.get('AZURE_OPENAI_EMBEDDING_ENDPOINT', None)
        azure_openai_api_version = os.environ.get('AZURE_OPENAI_EMBEDDING_API_VERSION', None)
        azure_openai_deployment_name = os.environ.get(
            'AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME', None
        )
        azure_openai_use_managed_identity = (
            os.environ.get('AZURE_OPENAI_USE_MANAGED_IDENTITY', 'false').lower() == 'true'
        )
        if azure_openai_endpoint is not None:
            # Setup for Azure OpenAI API
            # Log if empty deployment name was provided
            azure_openai_deployment_name = os.environ.get(
                'AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME', None
            )
            if azure_openai_deployment_name is None:
                logger.error('AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME environment variable not set')

                raise ValueError(
                    'AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME environment variable not set'
                )

            if not azure_openai_use_managed_identity:
                # api key
                api_key = os.environ.get('AZURE_OPENAI_EMBEDDING_API_KEY', None) or os.environ.get(
                    'OPENAI_API_KEY', None
                )
            else:
                # Managed identity
                api_key = None

            return cls(
                azure_openai_use_managed_identity=azure_openai_use_managed_identity,
                azure_openai_endpoint=azure_openai_endpoint,
                api_key=api_key,
                azure_openai_api_version=azure_openai_api_version,
                azure_openai_deployment_name=azure_openai_deployment_name,
            )
        else:
            return cls(
                model=model,
                api_key=os.environ.get('OPENAI_API_KEY'),
                base_url=base_url,
            )

    def create_client(self) -> EmbedderClient | None:
        if self.azure_openai_endpoint is not None:
            # Azure OpenAI API setup
            if self.azure_openai_use_managed_identity:
                # Use managed identity for authentication
                token_provider = create_azure_credential_token_provider()
                return AzureOpenAIEmbedderClient(
                    azure_client=AsyncAzureOpenAI(
                        azure_endpoint=self.azure_openai_endpoint,
                        azure_deployment=self.azure_openai_deployment_name,
                        api_version=self.azure_openai_api_version,
                        azure_ad_token_provider=token_provider,
                    ),
                    model=self.model,
                )
            elif self.api_key:
                # Use API key for authentication
                return AzureOpenAIEmbedderClient(
                    azure_client=AsyncAzureOpenAI(
                        azure_endpoint=self.azure_openai_endpoint,
                        azure_deployment=self.azure_openai_deployment_name,
                        api_version=self.azure_openai_api_version,
                        api_key=self.api_key,
                    ),
                    model=self.model,
                )
            else:
                logger.error('OPENAI_API_KEY must be set when using Azure OpenAI API')
                return None
        elif self.base_url:
            # Local embedding server (llama.cpp, TEI, etc.)
            # Primary: local server. Fallback: OpenAI API (if API key available).
            primary = OpenAIEmbedder(
                config=OpenAIEmbedderConfig(
                    api_key=self.api_key or 'not-needed',
                    embedding_model=self.model,
                    base_url=self.base_url,
                )
            )

            if self.api_key:
                # Wrap with fallback to OpenAI API for when local server is down.
                # Note: different models produce incompatible vector spaces —
                # vector search degrades during fallback, BM25 fulltext still works.
                fallback = OpenAIEmbedder(
                    config=OpenAIEmbedderConfig(
                        api_key=self.api_key,
                        embedding_model=DEFAULT_EMBEDDER_MODEL,
                    )
                )
                logger.info(
                    f'Using local embedder at {self.base_url} '
                    f'with OpenAI API fallback ({DEFAULT_EMBEDDER_MODEL})'
                )
                return FallbackEmbedder(primary=primary, fallback=fallback)

            logger.info(f'Using local embedder at {self.base_url} (no fallback)')
            return primary
        else:
            # OpenAI API setup
            if not self.api_key:
                return None

            embedder_config = OpenAIEmbedderConfig(api_key=self.api_key, embedding_model=self.model)

            return OpenAIEmbedder(config=embedder_config)


class Neo4jConfig(BaseModel):
    """Configuration for Neo4j database connection."""

    uri: str = 'bolt://localhost:7687'
    user: str = 'neo4j'
    password: str = 'password'

    @classmethod
    def from_env(cls) -> 'Neo4jConfig':
        """Create Neo4j configuration from environment variables."""
        return cls(
            uri=os.environ.get('NEO4J_URI', 'bolt://localhost:7687'),
            user=os.environ.get('NEO4J_USER', 'neo4j'),
            password=os.environ.get('NEO4J_PASSWORD', 'password'),
        )


class GraphitiConfig(BaseModel):
    """Configuration for Graphiti client.

    Centralizes all configuration parameters for the Graphiti client.
    """

    llm: GraphitiLLMConfig = Field(default_factory=GraphitiLLMConfig)
    embedder: GraphitiEmbedderConfig = Field(default_factory=GraphitiEmbedderConfig)
    neo4j: Neo4jConfig = Field(default_factory=Neo4jConfig)
    group_id: str | None = None
    use_custom_entities: bool = False
    destroy_graph: bool = False

    @classmethod
    def from_env(cls) -> 'GraphitiConfig':
        """Create a configuration instance from environment variables."""
        return cls(
            llm=GraphitiLLMConfig.from_env(),
            embedder=GraphitiEmbedderConfig.from_env(),
            neo4j=Neo4jConfig.from_env(),
        )

    @classmethod
    def from_cli_and_env(cls, args: argparse.Namespace) -> 'GraphitiConfig':
        """Create configuration from CLI arguments, falling back to environment variables."""
        # Start with environment configuration
        config = cls.from_env()

        # Apply CLI overrides
        if args.group_id:
            config.group_id = args.group_id
        else:
            config.group_id = 'default'

        config.use_custom_entities = args.use_custom_entities
        config.destroy_graph = args.destroy_graph

        # Update LLM config using CLI args
        config.llm = GraphitiLLMConfig.from_cli_and_env(args)

        return config


class MCPConfig(BaseModel):
    """Configuration for MCP server."""

    transport: str = 'sse'  # Default to SSE transport

    @classmethod
    def from_cli(cls, args: argparse.Namespace) -> 'MCPConfig':
        """Create MCP configuration from CLI arguments."""
        return cls(transport=args.transport)


# Configure logging — stderr (INFO for CC) + file (WARNING+ for troubleshooting)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

# File logging — errors/warnings only, rotated to prevent disk fill
_log_dir = Path.home() / '.graphiti' / 'logs'
_log_dir.mkdir(parents=True, exist_ok=True)
_file_handler = logging.handlers.RotatingFileHandler(
    _log_dir / 'mcp-server.log',
    maxBytes=5 * 1024 * 1024,  # 5 MB
    backupCount=3,             # 15 MB max total
    encoding='utf-8',
)
_file_handler.setLevel(logging.WARNING)
_file_handler.setFormatter(logging.Formatter(
    '%(asctime)s [%(levelname)s] %(name)s — %(message)s'
))
logging.getLogger().addHandler(_file_handler)  # root logger, catches all modules

# Create global config instance - will be properly initialized later
config = GraphitiConfig()

# MCP server instructions
GRAPHITI_MCP_INSTRUCTIONS = """
Graphiti is a memory service for AI agents built on a knowledge graph. Graphiti performs well
with dynamic data such as user interactions, changing enterprise data, and external information.

Graphiti transforms information into a richly connected knowledge network, allowing you to 
capture relationships between concepts, entities, and information. The system organizes data as episodes 
(content snippets), nodes (entities), and facts (relationships between entities), creating a dynamic, 
queryable memory store that evolves with new information. Graphiti supports multiple data formats, including 
structured JSON data, enabling seamless integration with existing data pipelines and systems.

Facts contain temporal metadata, allowing you to track the time of creation and whether a fact is invalid 
(superseded by new information).

Key capabilities:
1. Add episodes (text, messages, or JSON) to the knowledge graph with the add_memory tool
2. Search for nodes (entities) in the graph using natural language queries with search_nodes
3. Find relevant facts (relationships between entities) with search_facts
4. Retrieve specific entity edges or episodes by UUID
5. Manage the knowledge graph with tools like delete_episode, delete_entity_edge, and clear_graph

The server connects to a database for persistent storage and uses language models for certain operations. 
Each piece of information is organized by group_id, allowing you to maintain separate knowledge domains.

When adding information, provide descriptive names and detailed content to improve search quality. 
When searching, use specific queries and consider filtering by group_id for more relevant results.

For optimal performance, ensure the database is properly configured and accessible, and valid 
API keys are provided for any language model operations.
"""

# MCP server instance
mcp = FastMCP(
    'Graphiti Agent Memory',
    instructions=GRAPHITI_MCP_INSTRUCTIONS,
)

# Initialize Graphiti client
graphiti_client: Graphiti | None = None


async def initialize_graphiti():
    """Initialize the Graphiti client with the configured settings."""
    global graphiti_client, config

    try:
        # Create LLM client if possible
        llm_client = config.llm.create_client()
        if not llm_client and config.use_custom_entities:
            # If custom entities are enabled, we must have an LLM client
            raise ValueError('OPENAI_API_KEY must be set when custom entities are enabled')

        # Validate Neo4j configuration
        if not config.neo4j.uri or not config.neo4j.user or not config.neo4j.password:
            raise ValueError('NEO4J_URI, NEO4J_USER, and NEO4J_PASSWORD must be set')

        embedder_client = config.embedder.create_client()

        # Create FalkorDB driver
        if USE_FALKORDBLITE:
            # FalkorDBLite: embedded FalkorDB without Docker
            # Data stored at ~/.graphiti/falkordblite.rdb by default
            falkordblite_path = os.path.expanduser(os.environ.get(
                'FALKORDBLITE_PATH',
                '~/.graphiti/falkordblite.rdb'
            ))
            # Ensure parent directory exists
            os.makedirs(os.path.dirname(falkordblite_path), exist_ok=True)
            logger.info(f'Using FalkorDBLite (embedded) at: {falkordblite_path}')
            embedded_db = EmbeddedAsyncFalkorDB(dbfilename=falkordblite_path)
            falkor_driver = FalkorDriver(falkor_db=embedded_db)
        else:
            # External FalkorDB-compatible server (official FalkorDB or Lite singleton).
            # Host defaults to localhost so existing local setups keep working.
            falkordb_host = os.environ.get('FALKORDB_HOST', '127.0.0.1')
            falkordb_port = int(
                os.environ.get('FALKORDB_PORT')
                or os.environ.get('FALKORDB_REDIS_PORT', '6379')
            )
            if os.environ.get('FALKORDB_REDIS_PORT') and not os.environ.get('FALKORDB_PORT'):
                logger.info('Using legacy FALKORDB_REDIS_PORT env var; prefer FALKORDB_PORT')
            logger.info(f'Using external FalkorDB at {falkordb_host}:{falkordb_port}')
            # Use config.group_id as database name so the driver queries the
            # correct per-project FalkorDB graph (FalkorDB uses separate named
            # graphs for multi-tenancy, unlike Neo4j which uses node properties).
            falkor_driver = FalkorDriver(
                host=falkordb_host,
                port=falkordb_port,
                password=os.environ.get('FALKORDB_PASSWORD'),
                database=config.group_id or 'default_db',
            )

        # Initialize Graphiti client with FalkorDB driver
        graphiti_client = Graphiti(
            llm_client=llm_client,
            embedder=embedder_client,
            graph_driver=falkor_driver,
            max_coroutines=SEMAPHORE_LIMIT,
        )

        # Destroy graph if requested
        if config.destroy_graph:
            logger.info('Destroying graph...')
            await clear_data(graphiti_client.driver)

        # Initialize the graph database with Graphiti's indices
        await graphiti_client.build_indices_and_constraints()
        logger.info('Graphiti client initialized successfully')

        # Log configuration details for transparency
        if llm_client:
            logger.info(f'Using OpenAI model: {config.llm.model}')
            logger.info(f'Using temperature: {config.llm.temperature}')
        else:
            logger.info('No LLM client configured - entity extraction will be limited')

        logger.info(f'Using group_id: {config.group_id}')
        logger.info(
            f'Custom entity extraction: {"enabled" if config.use_custom_entities else "disabled"}'
        )
        logger.info(f'Using concurrency limit: {SEMAPHORE_LIMIT}')

    except Exception as e:
        logger.error(f'Failed to initialize Graphiti: {str(e)}')
        raise


def get_effective_group_id(ctx: Context | None) -> str:
    """Extract group_id from SSE connection with fallback logic.

    Priority order:
    1. Custom HTTP header X-Graphiti-Group-Id (sent with every MCP message)
    2. SSE URL query parameter ?group_id=... (handshake only, kept for compatibility)
    3. Config group_id (from CLI --group-id flag)
    4. Hard-coded "default"

    Args:
        ctx: FastMCP Context object (may be None if called outside request)

    Returns:
        The group_id to use for Graphiti operations
    """
    # Priority 1: Extract from custom header (sent with every MCP message)
    if ctx is not None:
        try:
            request = ctx.request_context.request
            if request and hasattr(request, 'headers'):
                # Headers are case-insensitive, try both forms
                header_group_id = request.headers.get('X-Graphiti-Group-Id') or request.headers.get('x-graphiti-group-id')
                if header_group_id and header_group_id.strip():
                    logger.debug(f"Using header-based group_id: {header_group_id}")
                    return header_group_id

            # Priority 2: Fall back to query parameter (SSE handshake only)
            if request and hasattr(request, 'query_params'):
                connection_group_id = request.query_params.get('group_id')
                if connection_group_id and connection_group_id.strip():
                    logger.debug(f"Using query-parameter group_id: {connection_group_id}")
                    return connection_group_id
        except (ValueError, AttributeError) as e:
            logger.debug(f"Could not extract group_id from context: {e}")

    # Priority 3: Fall back to config.group_id (from CLI --group-id)
    if config.group_id:
        logger.debug(f"Using config group_id: {config.group_id}")
        return config.group_id

    # Priority 4: Final fallback
    logger.debug("Using default group_id (no connection/config override)")
    return "default"


def format_fact_result(edge: EntityEdge) -> dict[str, Any]:
    """Format an entity edge into a readable result.

    Since EntityEdge is a Pydantic BaseModel, we can use its built-in serialization capabilities.

    Args:
        edge: The EntityEdge to format

    Returns:
        A dictionary representation of the edge with serialized dates and excluded embeddings
    """
    result = edge.model_dump(
        mode='json',
        exclude={
            'fact_embedding',
        },
    )
    result.get('attributes', {}).pop('fact_embedding', None)
    return result


# Dictionary to store queues for each group_id
# Each queue is a list of tasks to be processed sequentially
episode_queues: dict[str, asyncio.Queue] = {}
# Dictionary to track if a worker is running for each group_id
queue_workers: dict[str, bool] = {}


# Strong references to worker tasks — prevents GC from collecting them mid-execution.
# See Graphiti #1176: asyncio.create_task() without strong ref = silent worker death.
# Python docs: "Important: Save a reference to the result of create_task(),
# otherwise the task may get garbage-collected mid-execution."
_worker_tasks: dict[str, asyncio.Task] = {}


async def process_episode_queue(group_id: str):
    """Process episodes for a specific group_id sequentially.

    This function runs as a long-lived task that processes episodes
    from the queue one at a time.

    IMPORTANT — No asyncio.wait_for around episode processing:
    asyncio.wait_for cancels tasks via CancelledError, which corrupts httpx
    connection pools permanently (httpcore #961, wontfix). httpx native timeouts
    (read=120s) handle individual API hangs safely at the socket level.
    Normal episode processing takes 150-400s (15-30+ sequential LLM calls).
    """
    global queue_workers

    logger.info(f'Starting episode queue worker for group_id: {group_id}')
    queue_workers[group_id] = True

    try:
        while True:
            # Get the next episode processing function from the queue
            # This will wait if the queue is empty
            process_func = await episode_queues[group_id].get()

            start_time = asyncio.get_event_loop().time()
            try:
                # No asyncio.wait_for — httpx native timeouts protect individual calls.
                # Cancelling mid-httpx-request corrupts the connection pool (httpcore #961).
                await process_func()
            except Exception as e:
                error_msg = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                error_msg = classify_processing_error(e, error_msg)
                logger.error(f'Error processing queued episode for group_id {group_id}: {error_msg}')
                record_error("episode_processing", error_msg, group_id=group_id)
            finally:
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed > 300:
                    logger.warning(
                        f'Episode processing for group_id {group_id} took {elapsed:.0f}s '
                        f'(>5 min — graph may be growing large, see Graphiti #1275)'
                    )
                # Mark the task as done regardless of success/failure
                episode_queues[group_id].task_done()
    except asyncio.CancelledError:
        logger.info(f'Episode queue worker for group_id {group_id} was cancelled')
        record_error("worker_cancelled", "Queue worker was cancelled", group_id=group_id)
    except Exception as e:
        logger.error(f'Unexpected error in queue worker for group_id {group_id}: {str(e)}')
        record_error("worker_crash", str(e), group_id=group_id)
        notify_episode_failure("WORKER_CRASH", group_id, f"Queue worker crashed: {str(e)}")
    finally:
        remaining = episode_queues[group_id].qsize() if group_id in episode_queues else 0
        queue_workers[group_id] = False
        _worker_tasks.pop(group_id, None)  # Clean up strong reference
        logger.info(f'Stopped episode queue worker for group_id: {group_id} ({remaining} episodes remaining in queue)')
        if remaining > 0:
            notify_episode_failure(
                f"{remaining} orphaned episodes",
                group_id,
                f"Queue worker stopped with {remaining} unprocessed episodes remaining"
            )


@mcp.tool()
async def add_memory(
    name: str,
    episode_body: str,
    ctx: Context,
    source: str = 'text',
    source_description: str = '',
    uuid: str = "",
    valid_at: str = "",  # Hidden param: ISO datetime string for historical entries
) -> SuccessResponse | ErrorResponse:
    """Add episode to CURRENT PROJECT memory graph.

    This function returns immediately and processes the episode addition in the background.
    Episodes for the same group_id are processed sequentially to avoid race conditions.

    Scope: Uses group_id from config (set by shims wrapper based on git project).
    Cannot leak data to other projects. Use add_global_memory() for shared knowledge.

    Args:
        name (str): Name of the episode
        episode_body (str): The content of the episode to persist to memory. When source='json', this must be a
                           properly escaped JSON string, not a raw Python dictionary. The JSON data will be
                           automatically processed to extract entities and relationships.
        source (str, optional): Source type, must be one of:
                               - 'text': For plain text content (default)
                               - 'json': For structured data
                               - 'message': For conversation-style content
        source_description (str, optional): Description of the source
        uuid (str, optional): Optional UUID for the episode

    Examples:
        # Adding plain text content
        add_memory(
            name="Company News",
            episode_body="Acme Corp announced a new product line today.",
            source="text",
            source_description="news article",
        )

        # Adding structured JSON data
        # NOTE: episode_body must be a properly escaped JSON string. Note the triple backslashes
        add_memory(
            name="Customer Profile",
            episode_body="{\\\"company\\\": {\\\"name\\\": \\\"Acme Technologies\\\"}, \\\"products\\\": [{\\\"id\\\": \\\"P001\\\", \\\"name\\\": \\\"CloudSync\\\"}, {\\\"id\\\": \\\"P002\\\", \\\"name\\\": \\\"DataMiner\\\"}]}",
            source="json",
            source_description="CRM data"
        )

        # Adding message-style content
        add_memory(
            name="Customer Conversation",
            episode_body="user: What's your return policy?\nassistant: You can return items within 30 days.",
            source="message",
            source_description="chat transcript",
        )

    Notes:
        When using source='json':
        - The JSON must be a properly escaped string, not a raw Python dictionary
        - The JSON will be automatically processed to extract entities and relationships
        - Complex nested structures are supported (arrays, nested objects, mixed data types), but keep nesting to a minimum
        - Entities will be created from appropriate JSON properties
        - Relationships between entities will be established based on the JSON structure
    """
    global graphiti_client, episode_queues, queue_workers

    if graphiti_client is None:
        return ErrorResponse(error='Graphiti client not initialized')

    try:
        # Map string source to EpisodeType enum
        source_type = EpisodeType.text
        if source.lower() == 'message':
            source_type = EpisodeType.message
        elif source.lower() == 'json':
            source_type = EpisodeType.json

        # Use connection-specific group_id (from SSE query param)
        effective_group_id = get_effective_group_id(ctx)

        # Cast group_id to str to satisfy type checker
        # The Graphiti client expects a str for group_id, not Optional[str]
        group_id_str = str(effective_group_id) if effective_group_id is not None else ''

        # We've already checked that graphiti_client is not None above
        # This assert statement helps type checkers understand that graphiti_client is defined
        assert graphiti_client is not None, 'graphiti_client should not be None here'

        # Use cast to help the type checker understand that graphiti_client is not None
        client = cast(Graphiti, graphiti_client)

        # Compute effective reference_time: use valid_at if provided, otherwise now
        if valid_at:
            try:
                # Parse ISO format string to datetime
                effective_reference_time = datetime.fromisoformat(valid_at.replace('Z', '+00:00'))
                if effective_reference_time.tzinfo is None:
                    effective_reference_time = effective_reference_time.replace(tzinfo=timezone.utc)
                logger.info(f"Using custom valid_at for episode '{name}': {effective_reference_time.isoformat()}")
            except ValueError as e:
                logger.warning(f"Invalid valid_at format '{valid_at}', using current time: {e}")
                effective_reference_time = datetime.now(timezone.utc)
        else:
            effective_reference_time = datetime.now(timezone.utc)

        # Define the episode processing function
        async def process_episode():
            try:
                logger.info(f"Processing queued episode '{name}' for group_id: {group_id_str}")
                # Use all entity types if use_custom_entities is enabled, otherwise use empty dict
                entity_types = ENTITY_TYPES if config.use_custom_entities else {}

                # No inner timeout — the outer process_episode_queue timeout handles hung calls.
                # Inner timeouts swallow errors and lose episodes silently.
                # Full add_episode pipeline (extract nodes + reflexion + resolve + edges + attributes)
                # makes 15-30+ sequential LLM calls at ~10s each = 150-400s normally.
                await client.add_episode(
                    name=name,
                    episode_body=episode_body,
                    source=source_type,
                    source_description=source_description,
                    group_id=group_id_str,  # Using the string version of group_id
                    uuid=uuid or None,
                    reference_time=effective_reference_time,
                    entity_types=entity_types,
                )
                logger.info(f"Episode '{name}' processed successfully")
            except Exception as e:
                # str(e) is empty for some exceptions (e.g. asyncio.TimeoutError)
                error_msg = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                # Disambiguate insufficient_quota (out of credit) from a real
                # transient rate limit — both arrive as a 429 / RateLimitError.
                error_msg = classify_processing_error(e, error_msg)
                logger.error(
                    f"Error processing episode '{name}' for group_id {group_id_str}: {error_msg}"
                )
                record_error("episode_processing", error_msg, group_id=group_id_str, episode_name=name)
                # Send macOS notification for failed episode
                notify_episode_failure(name, group_id_str, error_msg)

        # Initialize queue for this group_id if it doesn't exist
        if group_id_str not in episode_queues:
            episode_queues[group_id_str] = asyncio.Queue()

        # Add the episode processing function to the queue
        await episode_queues[group_id_str].put(process_episode)

        # Start a worker for this queue if one isn't already running
        if not queue_workers.get(group_id_str, False):
            task = asyncio.create_task(process_episode_queue(group_id_str))
            _worker_tasks[group_id_str] = task  # Strong ref prevents GC (Graphiti #1176)

        # Return immediately with a success message
        return SuccessResponse(
            message=f"Episode '{name}' queued for processing (position: {episode_queues[group_id_str].qsize()})"
        )
    except Exception as e:
        error_msg = str(e)
        logger.error(f'Error queuing episode task: {error_msg}')
        return ErrorResponse(error=f'Error queuing episode task: {error_msg}')


@mcp.tool()
async def add_global_memory(
    name: str,
    episode_body: str,
    ctx: Context,
    source: str = 'text',
    source_description: str = '',
    uuid: str = "",
    valid_at: str = "",  # Hidden param: ISO datetime string for historical entries
) -> SuccessResponse | ErrorResponse:
    """Add episode to GLOBAL memory graph (shared across all projects).

    This function returns immediately and processes the episode addition in the background.
    Episodes for the same group_id are processed sequentially to avoid race conditions.

    Scope: Hardcoded to 'default' group (cross-project shared knowledge).
    Use for general knowledge not specific to any project.

    Args:
        name (str): Name of the episode
        episode_body (str): The content of the episode to persist to memory. When source='json', this must be a
                           properly escaped JSON string, not a raw Python dictionary. The JSON data will be
                           automatically processed to extract entities and relationships.
        source (str, optional): Source type, must be one of:
                               - 'text': For plain text content (default)
                               - 'json': For structured data
                               - 'message': For conversation-style content
        source_description (str, optional): Description of the source
        uuid (str, optional): Optional UUID for the episode

    Examples:
        # Adding general knowledge
        add_global_memory(
            name="Python Best Practices",
            episode_body="Always use type hints and docstrings for better code quality.",
            source="text",
            source_description="general development knowledge",
        )
    """
    global graphiti_client, episode_queues, queue_workers

    if graphiti_client is None:
        return ErrorResponse(error='Graphiti client not initialized')

    try:
        # Map string source to EpisodeType enum
        source_type = EpisodeType.text
        if source.lower() == 'message':
            source_type = EpisodeType.message
        elif source.lower() == 'json':
            source_type = EpisodeType.json

        # Use HARDCODED 'default' for global scoping
        effective_group_id = "default"

        # Cast group_id to str to satisfy type checker
        group_id_str = str(effective_group_id)

        # We've already checked that graphiti_client is not None above
        assert graphiti_client is not None, 'graphiti_client should not be None here'

        # Use cast to help the type checker understand that graphiti_client is not None
        client = cast(Graphiti, graphiti_client)

        # Compute effective reference_time: use valid_at if provided, otherwise now
        if valid_at:
            try:
                # Parse ISO format string to datetime
                effective_reference_time = datetime.fromisoformat(valid_at.replace('Z', '+00:00'))
                if effective_reference_time.tzinfo is None:
                    effective_reference_time = effective_reference_time.replace(tzinfo=timezone.utc)
                logger.info(f"Using custom valid_at for global episode '{name}': {effective_reference_time.isoformat()}")
            except ValueError as e:
                logger.warning(f"Invalid valid_at format '{valid_at}', using current time: {e}")
                effective_reference_time = datetime.now(timezone.utc)
        else:
            effective_reference_time = datetime.now(timezone.utc)

        # Define the episode processing function
        async def process_episode():
            try:
                logger.info(f"Processing queued episode '{name}' for GLOBAL group_id: {group_id_str}")
                # Use all entity types if use_custom_entities is enabled, otherwise use empty dict
                entity_types = ENTITY_TYPES if config.use_custom_entities else {}

                await client.add_episode(
                    name=name,
                    episode_body=episode_body,
                    source=source_type,
                    source_description=source_description,
                    group_id=group_id_str,
                    uuid=uuid or None,
                    reference_time=effective_reference_time,
                    entity_types=entity_types,
                )
                logger.info(f"Global episode '{name}' added successfully")
            except Exception as e:
                error_msg = str(e)
                logger.error(
                    f"Error processing global episode '{name}' for group_id {group_id_str}: {error_msg}"
                )
                # Send macOS notification for failed episode
                notify_episode_failure(name, group_id_str, error_msg)

        # Initialize queue for this group_id if it doesn't exist
        if group_id_str not in episode_queues:
            episode_queues[group_id_str] = asyncio.Queue()

        # Add the episode processing function to the queue
        await episode_queues[group_id_str].put(process_episode)

        # Start a worker for this queue if one isn't already running
        if not queue_workers.get(group_id_str, False):
            task = asyncio.create_task(process_episode_queue(group_id_str))
            _worker_tasks[group_id_str] = task  # Strong ref prevents GC (Graphiti #1176)

        # Return immediately with a success message
        return SuccessResponse(
            message=f"Global episode '{name}' queued for processing (position: {episode_queues[group_id_str].qsize()})"
        )
    except Exception as e:
        error_msg = str(e)
        logger.error(f'Error queuing global episode task: {error_msg}')
        return ErrorResponse(error=f'Error queuing global episode task: {error_msg}')


@mcp.tool()
async def search_nodes(
    query: str,
    ctx: Context,
    max_nodes: int = 10,
    center_node_uuid: str = "",
    entity: str = '',  # cursor seems to break with None
) -> NodeSearchResponse | ErrorResponse:
    """Search nodes in CURRENT PROJECT memory graph.

    Scope: Uses group_id from config (set by shims wrapper based on git project).
    Cannot access other projects. Use search_cross_project_nodes() for multi-project queries.

    Note: entity is a single entity type to filter results (permitted: "Preference", "Procedure").

    Args:
        query: The search query
        max_nodes: Maximum number of nodes to return (default: 10)
        center_node_uuid: Optional UUID of a node to center the search around
        entity: Optional single entity type to filter results (permitted: "Preference", "Procedure")
    """
    global graphiti_client

    if graphiti_client is None:
        return ErrorResponse(error='Graphiti client not initialized')

    try:
        # Use connection-specific group_id (from SSE query param)
        effective_group_id = get_effective_group_id(ctx)
        effective_group_ids = [effective_group_id]

        # Configure the search
        if center_node_uuid is not None:
            search_config = NODE_HYBRID_SEARCH_NODE_DISTANCE.model_copy(deep=True)
        else:
            search_config = NODE_HYBRID_SEARCH_RRF.model_copy(deep=True)
        search_config.limit = max_nodes

        filters = SearchFilters()
        if entity != '':
            filters.node_labels = [entity]

        # We've already checked that graphiti_client is not None above
        assert graphiti_client is not None

        # Use cast to help the type checker understand that graphiti_client is not None
        client = cast(Graphiti, graphiti_client)

        # Perform the search using the _search method
        search_results = await client._search(
            query=query,
            config=search_config,
            group_ids=effective_group_ids,
            center_node_uuid=center_node_uuid or None,
            search_filter=filters,
        )

        if not search_results.nodes:
            return NodeSearchResponse(message='No relevant nodes found', nodes=[])

        # Format the node results
        formatted_nodes: list[NodeResult] = [
            {
                'uuid': node.uuid,
                'name': node.name,
                'summary': node.summary if hasattr(node, 'summary') else '',
                'labels': node.labels if hasattr(node, 'labels') else [],
                'group_id': node.group_id,
                'created_at': node.created_at.isoformat(),
                'attributes': node.attributes if hasattr(node, 'attributes') else {},
            }
            for node in search_results.nodes
        ]

        return NodeSearchResponse(message='Nodes retrieved successfully', nodes=formatted_nodes)
    except Exception as e:
        error_msg = str(e)
        logger.error(f'Error searching nodes: {error_msg}')
        return ErrorResponse(error=f'Error searching nodes: {error_msg}')


@mcp.tool()
async def search_global_nodes(
    query: str,
    ctx: Context,
    max_nodes: int = 10,
    center_node_uuid: str = "",
    entity: str = '',
) -> NodeSearchResponse | ErrorResponse:
    """Search nodes in GLOBAL memory graph (shared across all projects).

    Scope: Hardcoded to 'default' group (cross-project shared knowledge).
    Use for searching general knowledge not specific to any project.

    Note: entity is a single entity type to filter results (permitted: "Preference", "Procedure").

    Args:
        query: The search query
        max_nodes: Maximum number of nodes to return (default: 10)
        center_node_uuid: Optional UUID of a node to center the search around
        entity: Optional single entity type to filter results (permitted: "Preference", "Procedure")
    """
    global graphiti_client

    if graphiti_client is None:
        return ErrorResponse(error='Graphiti client not initialized')

    try:
        # Use HARDCODED 'default' for global scoping
        effective_group_ids = ["default"]

        # Configure the search
        if center_node_uuid is not None:
            search_config = NODE_HYBRID_SEARCH_NODE_DISTANCE.model_copy(deep=True)
        else:
            search_config = NODE_HYBRID_SEARCH_RRF.model_copy(deep=True)
        search_config.limit = max_nodes

        filters = SearchFilters()
        if entity != '':
            filters.node_labels = [entity]

        # We've already checked that graphiti_client is not None above
        assert graphiti_client is not None

        # Use cast to help the type checker understand that graphiti_client is not None
        client = cast(Graphiti, graphiti_client)

        # Perform the search using the _search method
        search_results = await client._search(
            query=query,
            config=search_config,
            group_ids=effective_group_ids,
            center_node_uuid=center_node_uuid or None,
            search_filter=filters,
        )

        if not search_results.nodes:
            return NodeSearchResponse(message='No relevant global nodes found', nodes=[])

        # Format the node results
        formatted_nodes = [format_node_result(node) for node in search_results.nodes]

        return NodeSearchResponse(message='Global nodes retrieved successfully', nodes=formatted_nodes)
    except Exception as e:
        error_msg = str(e)
        logger.error(f'Error searching global nodes: {error_msg}')
        return ErrorResponse(error=f'Error searching global nodes: {error_msg}')


@mcp.tool()
async def search_facts(
    query: str,
    ctx: Context,
    max_facts: int = 10,
    center_node_uuid: str = "",
) -> FactSearchResponse | ErrorResponse:
    """Search facts in CURRENT PROJECT memory graph.

    Scope: Uses group_id from config (set by shims wrapper based on git project).
    Cannot access other projects. Use search_cross_project_facts() for multi-project queries.

    Args:
        query: The search query
        max_facts: Maximum number of facts to return (default: 10)
        center_node_uuid: Optional UUID of a node to center the search around
    """
    global graphiti_client

    if graphiti_client is None:
        return ErrorResponse(error='Graphiti client not initialized')

    try:
        # Validate max_facts parameter
        if max_facts <= 0:
            return ErrorResponse(error='max_facts must be a positive integer')

        # Use connection-specific group_id (from SSE query param)
        effective_group_id = get_effective_group_id(ctx)
        effective_group_ids = [effective_group_id]

        # We've already checked that graphiti_client is not None above
        assert graphiti_client is not None

        # Use cast to help the type checker understand that graphiti_client is not None
        client = cast(Graphiti, graphiti_client)

        relevant_edges = await client.search(
            group_ids=effective_group_ids,
            query=query,
            num_results=max_facts,
            center_node_uuid=center_node_uuid or None,
        )

        if not relevant_edges:
            return FactSearchResponse(message='No relevant facts found', facts=[])

        facts = [format_fact_result(edge) for edge in relevant_edges]
        return FactSearchResponse(message='Facts retrieved successfully', facts=facts)
    except Exception as e:
        error_msg = str(e)
        logger.error(f'Error searching facts: {error_msg}')
        return ErrorResponse(error=f'Error searching facts: {error_msg}')


@mcp.tool()
async def search_global_facts(
    query: str,
    ctx: Context,
    max_facts: int = 10,
    center_node_uuid: str = "",
) -> FactSearchResponse | ErrorResponse:
    """Search facts in GLOBAL memory graph (shared across all projects).

    Scope: Hardcoded to 'default' group (cross-project shared knowledge).
    Use for searching general knowledge not specific to any project.

    Args:
        query: The search query
        max_facts: Maximum number of facts to return (default: 10)
        center_node_uuid: Optional UUID of a node to center the search around
    """
    global graphiti_client

    if graphiti_client is None:
        return ErrorResponse(error='Graphiti client not initialized')

    try:
        # Validate max_facts parameter
        if max_facts <= 0:
            return ErrorResponse(error='max_facts must be a positive integer')

        # Use HARDCODED 'default' for global scoping
        effective_group_ids = ["default"]

        # We've already checked that graphiti_client is not None above
        assert graphiti_client is not None

        # Use cast to help the type checker understand that graphiti_client is not None
        client = cast(Graphiti, graphiti_client)

        relevant_edges = await client.search(
            group_ids=effective_group_ids,
            query=query,
            num_results=max_facts,
            center_node_uuid=center_node_uuid or None,
        )

        if not relevant_edges:
            return FactSearchResponse(message='No relevant global facts found', facts=[])

        facts = [format_fact_result(edge) for edge in relevant_edges]
        return FactSearchResponse(message='Global facts retrieved successfully', facts=facts)
    except Exception as e:
        error_msg = str(e)
        logger.error(f'Error searching global facts: {error_msg}')
        return ErrorResponse(error=f'Error searching global facts: {error_msg}')


@mcp.tool()
async def search_cross_project_nodes(
    query: str,
    projects: list[str],
    ctx: Context,
    max_nodes: int = 10,
    center_node_uuid: str = "",
    entity: str = '',
) -> NodeSearchResponse | ErrorResponse:
    """Search nodes across MULTIPLE PROJECTS.

    Scope: Explicit list of project group_ids.
    Use for cross-project pattern analysis (rare).

    Example:
        search_cross_project_nodes(
            query="authentication implementation",
            projects=["dotfiles", "kimonokittens", "brf-auto"]
        )

    Note: entity is a single entity type to filter results (permitted: "Preference", "Procedure").

    Args:
        query: The search query
        projects: REQUIRED - explicit list of project group_ids to search
        max_nodes: Maximum number of nodes to return (default: 10)
        center_node_uuid: Optional UUID of a node to center the search around
        entity: Optional single entity type to filter results (permitted: "Preference", "Procedure")
    """
    global graphiti_client

    if graphiti_client is None:
        return ErrorResponse(error='Graphiti client not initialized')

    # Validate projects parameter
    if not projects:
        return ErrorResponse(error="Must provide explicit list of projects for cross-project search")

    try:
        # Use explicit project list for cross-project scoping
        effective_group_ids = projects

        # Configure the search
        if center_node_uuid is not None:
            search_config = NODE_HYBRID_SEARCH_NODE_DISTANCE.model_copy(deep=True)
        else:
            search_config = NODE_HYBRID_SEARCH_RRF.model_copy(deep=True)
        search_config.limit = max_nodes

        filters = SearchFilters()
        if entity != '':
            filters.node_labels = [entity]

        # We've already checked that graphiti_client is not None above
        assert graphiti_client is not None

        # Use cast to help the type checker understand that graphiti_client is not None
        client = cast(Graphiti, graphiti_client)

        # Perform the search using the _search method
        search_results = await client._search(
            query=query,
            config=search_config,
            group_ids=effective_group_ids,
            center_node_uuid=center_node_uuid or None,
            search_filter=filters,
        )

        if not search_results.nodes:
            return NodeSearchResponse(message=f'No relevant nodes found in projects: {", ".join(projects)}', nodes=[])

        # Format the node results
        formatted_nodes = [format_node_result(node) for node in search_results.nodes]

        return NodeSearchResponse(
            message=f'Nodes retrieved successfully from {len(projects)} projects',
            nodes=formatted_nodes
        )
    except Exception as e:
        error_msg = str(e)
        logger.error(f'Error searching cross-project nodes: {error_msg}')
        return ErrorResponse(error=f'Error searching cross-project nodes: {error_msg}')


@mcp.tool()
async def search_cross_project_facts(
    query: str,
    projects: list[str],
    ctx: Context,
    max_facts: int = 10,
    center_node_uuid: str = "",
) -> FactSearchResponse | ErrorResponse:
    """Search facts across MULTIPLE PROJECTS.

    Scope: Explicit list of project group_ids.
    Use for cross-project pattern analysis (rare).

    Example:
        search_cross_project_facts(
            query="API error handling patterns",
            projects=["dotfiles", "kimonokittens", "brf-auto"]
        )

    Args:
        query: The search query
        projects: REQUIRED - explicit list of project group_ids to search
        max_facts: Maximum number of facts to return (default: 10)
        center_node_uuid: Optional UUID of a node to center the search around
    """
    global graphiti_client

    if graphiti_client is None:
        return ErrorResponse(error='Graphiti client not initialized')

    # Validate projects parameter
    if not projects:
        return ErrorResponse(error="Must provide explicit list of projects for cross-project search")

    try:
        # Validate max_facts parameter
        if max_facts <= 0:
            return ErrorResponse(error='max_facts must be a positive integer')

        # Use explicit project list for cross-project scoping
        effective_group_ids = projects

        # We've already checked that graphiti_client is not None above
        assert graphiti_client is not None

        # Use cast to help the type checker understand that graphiti_client is not None
        client = cast(Graphiti, graphiti_client)

        relevant_edges = await client.search(
            group_ids=effective_group_ids,
            query=query,
            num_results=max_facts,
            center_node_uuid=center_node_uuid or None,
        )

        if not relevant_edges:
            return FactSearchResponse(
                message=f'No relevant facts found in projects: {", ".join(projects)}',
                facts=[]
            )

        facts = [format_fact_result(edge) for edge in relevant_edges]
        return FactSearchResponse(
            message=f'Facts retrieved successfully from {len(projects)} projects',
            facts=facts
        )
    except Exception as e:
        error_msg = str(e)
        logger.error(f'Error searching cross-project facts: {error_msg}')
        return ErrorResponse(error=f'Error searching cross-project facts: {error_msg}')


@mcp.tool()
async def delete_entity_edge(uuid: str, ctx: Context) -> SuccessResponse | ErrorResponse:
    """Delete an entity edge from the graph memory.

    Args:
        uuid: UUID of the entity edge to delete
    """
    global graphiti_client

    if graphiti_client is None:
        return ErrorResponse(error='Graphiti client not initialized')

    try:
        # We've already checked that graphiti_client is not None above
        assert graphiti_client is not None

        # Use cast to help the type checker understand that graphiti_client is not None
        client = cast(Graphiti, graphiti_client)

        # Use connection-specific group_id to clone the driver for the correct graph
        group_id = get_effective_group_id(ctx)
        driver = client.driver.clone(database=group_id)

        # Get the entity edge by UUID
        entity_edge = await EntityEdge.get_by_uuid(driver, uuid)
        # Delete the edge using its delete method
        await entity_edge.delete(driver)
        return SuccessResponse(message=f'Entity edge with UUID {uuid} deleted successfully')
    except Exception as e:
        error_msg = str(e)
        logger.error(f'Error deleting entity edge: {error_msg}')
        return ErrorResponse(error=f'Error deleting entity edge: {error_msg}')


@mcp.tool()
async def delete_episode(uuid: str, ctx: Context) -> SuccessResponse | ErrorResponse:
    """Delete an episode from the graph memory.

    Args:
        uuid: UUID of the episode to delete
    """
    global graphiti_client

    if graphiti_client is None:
        return ErrorResponse(error='Graphiti client not initialized')

    try:
        # We've already checked that graphiti_client is not None above
        assert graphiti_client is not None

        # Use cast to help the type checker understand that graphiti_client is not None
        client = cast(Graphiti, graphiti_client)

        # Use connection-specific group_id to clone the driver for the correct graph
        group_id = get_effective_group_id(ctx)
        driver = client.driver.clone(database=group_id)

        # Get the episodic node by UUID - EpisodicNode is already imported at the top
        episodic_node = await EpisodicNode.get_by_uuid(driver, uuid)
        # Delete the node using its delete method
        await episodic_node.delete(driver)
        return SuccessResponse(message=f'Episode with UUID {uuid} deleted successfully')
    except Exception as e:
        error_msg = str(e)
        logger.error(f'Error deleting episode: {error_msg}')
        return ErrorResponse(error=f'Error deleting episode: {error_msg}')


@mcp.tool()
async def get_entity_edge(uuid: str, ctx: Context) -> dict[str, Any] | ErrorResponse:
    """Get an entity edge from the graph memory by its UUID.

    Args:
        uuid: UUID of the entity edge to retrieve
    """
    global graphiti_client

    if graphiti_client is None:
        return ErrorResponse(error='Graphiti client not initialized')

    try:
        # We've already checked that graphiti_client is not None above
        assert graphiti_client is not None

        # Use cast to help the type checker understand that graphiti_client is not None
        client = cast(Graphiti, graphiti_client)

        # Use connection-specific group_id to clone the driver for the correct graph
        group_id = get_effective_group_id(ctx)
        driver = client.driver.clone(database=group_id)

        # Get the entity edge directly using the EntityEdge class method
        entity_edge = await EntityEdge.get_by_uuid(driver, uuid)

        # Use the format_fact_result function to serialize the edge
        # Return the Python dict directly - MCP will handle serialization
        return format_fact_result(entity_edge)
    except Exception as e:
        error_msg = str(e)
        logger.error(f'Error getting entity edge: {error_msg}')
        return ErrorResponse(error=f'Error getting entity edge: {error_msg}')


@mcp.tool()
async def get_episodes(
    ctx: Context,
    group_id: str = "",
    last_n: int = 10,
) -> list[dict[str, Any]] | EpisodeSearchResponse | ErrorResponse:
    """Get the most recent memory episodes for CURRENT PROJECT.

    Scope: Uses group_id from config (set by shims wrapper based on git project).
    Cannot access other projects. Use get_global_episodes() for shared knowledge.

    Args:
        group_id: ID of the group to retrieve episodes from. If not provided, uses the default group_id.
        last_n: Number of most recent episodes to retrieve (default: 10)
    """
    global graphiti_client

    if graphiti_client is None:
        return ErrorResponse(error='Graphiti client not initialized')

    try:
        # Use the provided group_id or fall back to the SSE context (project-scoped)
        effective_group_id = group_id if group_id else get_effective_group_id(ctx)

        if not isinstance(effective_group_id, str):
            return ErrorResponse(error='Group ID must be a string')

        # We've already checked that graphiti_client is not None above
        assert graphiti_client is not None

        # Use cast to help the type checker understand that graphiti_client is not None
        client = cast(Graphiti, graphiti_client)

        episodes = await client.retrieve_episodes(
            group_ids=[effective_group_id], last_n=last_n, reference_time=datetime.now(timezone.utc)
        )

        if not episodes:
            return EpisodeSearchResponse(
                message=f'No episodes found for group {effective_group_id}', episodes=[]
            )

        # Use Pydantic's model_dump method for EpisodicNode serialization
        formatted_episodes = [
            # Use mode='json' to handle datetime serialization
            episode.model_dump(mode='json')
            for episode in episodes
        ]

        # Return the Python list directly - MCP will handle serialization
        return formatted_episodes
    except Exception as e:
        error_msg = str(e)
        logger.error(f'Error getting episodes: {error_msg}')
        return ErrorResponse(error=f'Error getting episodes: {error_msg}')


@mcp.tool()
async def get_global_episodes(
    last_n: int = 10,
) -> list[dict[str, Any]] | EpisodeSearchResponse | ErrorResponse:
    """Get the most recent memory episodes from GLOBAL memory graph (shared across all projects).

    Scope: Hardcoded to 'default' group (cross-project shared knowledge).
    Use for retrieving general knowledge not specific to any project.

    Args:
        last_n: Number of most recent episodes to retrieve (default: 10)
    """
    global graphiti_client

    if graphiti_client is None:
        return ErrorResponse(error='Graphiti client not initialized')

    try:
        # Use HARDCODED 'default' for global scoping
        effective_group_id = 'default'

        # We've already checked that graphiti_client is not None above
        assert graphiti_client is not None

        # Use cast to help the type checker understand that graphiti_client is not None
        client = cast(Graphiti, graphiti_client)

        episodes = await client.retrieve_episodes(
            group_ids=[effective_group_id], last_n=last_n, reference_time=datetime.now(timezone.utc)
        )

        if not episodes:
            return EpisodeSearchResponse(
                message=f'No episodes found in global memory', episodes=[]
            )

        # Use Pydantic's model_dump method for EpisodicNode serialization
        formatted_episodes = [
            # Use mode='json' to handle datetime serialization
            episode.model_dump(mode='json')
            for episode in episodes
        ]

        # Return the Python list directly - MCP will handle serialization
        return formatted_episodes
    except Exception as e:
        error_msg = str(e)
        logger.error(f'Error getting global episodes: {error_msg}')
        return ErrorResponse(error=f'Error getting global episodes: {error_msg}')


@mcp.tool()
async def clear_graph(ctx: Context) -> SuccessResponse | ErrorResponse:
    """Clear all data from the CURRENT PROJECT graph memory and rebuild indices.

    Scope: Uses group_id from config (set by shims wrapper based on git project).
    Only clears the requesting session's graph, not other projects.
    """
    global graphiti_client

    if graphiti_client is None:
        return ErrorResponse(error='Graphiti client not initialized')

    try:
        # We've already checked that graphiti_client is not None above
        assert graphiti_client is not None

        # Use cast to help the type checker understand that graphiti_client is not None
        client = cast(Graphiti, graphiti_client)

        # Use connection-specific group_id to clone the driver for the correct graph
        group_id = get_effective_group_id(ctx)
        driver = client.driver.clone(database=group_id)

        # clear_data is already imported at the top
        await clear_data(driver)
        # Rebuild indices on the same scoped driver (not client.driver which may point to wrong graph)
        await build_indices_and_constraints(driver)
        return SuccessResponse(message=f'Graph for {group_id} cleared successfully and indices rebuilt')
    except Exception as e:
        error_msg = str(e)
        logger.error(f'Error clearing graph: {error_msg}')
        return ErrorResponse(error=f'Error clearing graph: {error_msg}')


@mcp.tool()
async def get_recent_errors(
    since_minutes: int = 60,
    error_type: str = "",
) -> list[dict[str, Any]]:
    """Get recent Graphiti processing errors.

    Useful for reviewing errors that occurred while you were away or in Focus mode.
    Returns errors from the in-memory accumulator (last 100 errors, ring buffer).

    Args:
        since_minutes: Return errors from last N minutes (default: 60)
        error_type: Optional filter by type. Empty string ("", the default) means
            "no filter, return all error types". Set to e.g. "episode_processing",
            "search", or "connection" to filter. Empty-string sentinel chosen over
            `str | None` because FastMCP's JSON-Schema layer rejects nullable
            optionals with -32602 InvalidParams (see docs/mcp_get_recent_errors_
            invalid_params_2026_06_04.md for the bug detail).

    Returns:
        List of error records with timestamp, episode_name, group_id, error_message, error_type
    """
    return get_recent_errors_list(
        since_minutes=since_minutes,
        error_type=error_type or None,  # empty string → None for backend filter logic
    )


@mcp.tool()
async def raw_cypher_query(
    query: str,
    ctx: Context,
    params: dict[str, Any] = {},  # noqa: B006 — FastMCP rejects nullable optionals (-32602)
    max_results: int = 50,
) -> list[dict[str, Any]] | ErrorResponse:
    """Execute a raw Cypher query against the graph database.

    Use for complex graph traversals that can't be expressed via search_nodes/search_facts.
    This is READ-ONLY - write operations (CREATE, DELETE, SET, MERGE) are blocked.

    Args:
        query: Valid Cypher query string. Must be read-only.
        params: Optional query parameters for parameterized queries (prevents injection).
        max_results: Maximum results to return (default 50, max 500).

    Returns:
        List of result dictionaries matching the RETURN clause.

    Example:
        raw_cypher_query(
            query="MATCH (d:Decision)-[:APPLIES_PATTERN]->(p:Pattern) "
                  "WHERE p.slug STARTS WITH $prefix "
                  "RETURN d.title, p.name, d.created_at "
                  "ORDER BY d.created_at DESC LIMIT $limit",
            params={"prefix": "auth__", "limit": 10}
        )

    Safety:
        - Write operations are blocked (CREATE, DELETE, SET, MERGE, REMOVE, DROP)
        - Results are capped at max_results to prevent memory issues
        - Parameterized queries are encouraged to prevent injection
    """
    global graphiti_client

    if graphiti_client is None:
        return ErrorResponse(error='Graphiti client not initialized')

    # Block write operations - use regex word boundaries to avoid false positives
    # e.g., "created_at" should NOT match "CREATE"
    query_upper = query.upper()
    write_keywords = [
        (r'\bCREATE\b', 'CREATE'),
        (r'\bDELETE\b', 'DELETE'),
        (r'\bSET\b', 'SET'),
        (r'\bMERGE\b', 'MERGE'),
        (r'\bREMOVE\b', 'REMOVE'),
        (r'\bDROP\b', 'DROP'),
        (r'\bDETACH\b', 'DETACH'),
    ]
    for pattern, keyword in write_keywords:
        if re.search(pattern, query_upper):
            return ErrorResponse(
                error=f'Write operations are not allowed. Found: {keyword}'
            )

    # Cap max_results
    max_results = min(max_results, 500)

    # Ensure LIMIT is present or add it
    if 'LIMIT' not in query_upper:
        query = f'{query} LIMIT {max_results}'

    try:
        client = cast(Graphiti, graphiti_client)

        # Use connection-specific group_id to clone the driver for the correct graph
        group_id = get_effective_group_id(ctx)
        driver = client.driver.clone(database=group_id)

        # Execute the query
        if params is None:
            params = {}

        result = await driver.execute_query(query, **params)

        if result is None:
            return []

        records, header, _ = result

        # Truncate to max_results
        if len(records) > max_results:
            records = records[:max_results]

        return records

    except Exception as e:
        error_msg = str(e)
        logger.error(f'Error executing Cypher query: {error_msg}\nQuery: {query}')
        return ErrorResponse(error=f'Cypher query error: {error_msg}')


@mcp.tool()
async def cypher_query_write(
    query: str,
    ctx: Context,
    params: dict[str, Any] = {},  # noqa: B006 — FastMCP rejects nullable optionals (-32602)
    max_results: int = 10000,
) -> list[dict[str, Any]] | ErrorResponse:
    """Execute a write-capable Cypher query against the user's group-scoped graph.

    Companion to raw_cypher_query — allows CREATE / DELETE / SET / MERGE /
    REMOVE / DETACH DELETE while still preventing catastrophic operations
    (DROP). Use for high-frequency programmatic CRUD where add_memory's
    LLM-extraction is overkill — e.g., the master-todo-system kanban
    (Fyr Tauri app, May 2026): known schema, structural CRUD, no LLM needed.

    Args:
        query: Valid Cypher query string. May include write operations.
        params: Optional query parameters for parameterized queries (prevents injection).
        max_results: Maximum result rows from RETURN clause (default 50, max 500).

    Returns:
        List of result dictionaries matching the RETURN clause (often the uuid
        of the created or updated node). Empty list if the query returns no rows.

    Safety:
        - Catastrophic ops blocked: DROP (database / graph / index level).
          For full graph wipe use the dedicated `clear_graph` tool instead.
        - Group_id scoping: query targets ONLY the calling user's graph
          (driver.clone(database=group_id) — same pattern as raw_cypher_query).
          Cross-user damage is not possible without DROP-level ops.
        - Parameterized queries strongly encouraged for injection-safety.
        - LIMIT is auto-added to RETURN clauses if missing (caps memory).

    Trade-off vs add_memory:
        - cypher_query_write: deterministic, fast, no LLM/quota dependency,
          but bypasses entity extraction + embedding generation. Tasks created
          via this path will NOT be findable via semantic-similarity search
          (no name_embedding). Use for structured CRUD on known schemas.
        - add_memory: full LLM ingestion (extraction + embedding + dedup),
          slower, quota-bound, but enriches the graph with relationships and
          enables semantic search. Use for natural-language episodes where the
          extraction layer's intelligence is the value.

    Example (CREATE a Task entity for the master-todo-system kanban):
        cypher_query_write(
            query=(
                "CREATE (n:Entity:Task {"
                "  uuid: $uuid, name: $title, group_id: $gid,"
                "  status: 'open', domain: $dom, created_at: $ts"
                "}) RETURN n.uuid AS uuid"
            ),
            params={"uuid": "abc...", "title": "Buy milk", "gid": "fyr-fredrik",
                    "dom": "HOUSE", "ts": 1730000000000}
        )

    Example (UPDATE status, idempotent):
        cypher_query_write(
            query=(
                "MATCH (n:Task {uuid: $uuid, group_id: $gid}) "
                "SET n.status = $status RETURN n.uuid AS uuid"
            ),
            params={"uuid": "abc...", "gid": "fyr-fredrik", "status": "done"}
        )
    """
    global graphiti_client

    if graphiti_client is None:
        return ErrorResponse(error='Graphiti client not initialized')

    # Block CATASTROPHIC operations only — writes (CREATE/DELETE/SET/MERGE/REMOVE/
    # DETACH DELETE) are explicitly allowed. DROP wipes a graph or database and
    # is reachable via clear_graph as a dedicated tool with stronger semantics.
    query_upper = query.upper()
    catastrophic_keywords = [
        (r'\bDROP\b', 'DROP'),
    ]
    for pattern, keyword in catastrophic_keywords:
        if re.search(pattern, query_upper):
            return ErrorResponse(
                error=(
                    f'Catastrophic operation not allowed in cypher_query_write: {keyword}. '
                    f'For full graph wipe use the dedicated clear_graph tool.'
                )
            )

    # Cap max_results.
    # Was 500 originally — bumped to 10000 (May 5, 2026) because Fyr's
    # master-todo-system listTasks query needs to fetch the full set of
    # existing Tasks (per external_source) to do its diff correctly. With
    # the 500 cap, scans of >500 tasks per source created duplicates on
    # every rescan because list_tasks returned only 500 of N existing,
    # and the diff classified the missing N-500 as "not in graph → CREATE
    # new". 10000 covers realistic personal-task sets for years; if a
    # future workload needs more, paginate via SKIP/LIMIT instead of
    # bumping again.
    max_results = min(max_results, 10000)

    # Auto-add LIMIT to RETURN clauses (writes commonly RETURN 1 row,
    # but bulk operations may RETURN many — cap them).
    if 'LIMIT' not in query_upper and 'RETURN' in query_upper:
        query = f'{query} LIMIT {max_results}'

    try:
        client = cast(Graphiti, graphiti_client)

        # Per-group scoping: clone driver to target the user's named FalkorDB graph
        group_id = get_effective_group_id(ctx)
        driver = client.driver.clone(database=group_id)

        # Audit log: writes are observable in journalctl for incident review
        logger.info(
            f'cypher_query_write executing on group_id={group_id}: {query[:200]}'
        )

        if params is None:
            params = {}

        result = await driver.execute_query(query, **params)

        if result is None:
            return []

        records, header, _ = result

        # Truncate to max_results (mirror raw_cypher_query behavior)
        if len(records) > max_results:
            records = records[:max_results]

        return records

    except Exception as e:
        error_msg = str(e)
        logger.error(
            f'Error executing cypher_query_write: {error_msg}\nQuery: {query}'
        )
        return ErrorResponse(error=f'Cypher query error: {error_msg}')


@mcp.tool()
async def get_queue_status() -> SuccessResponse:
    """Check episode processing queue health.

    Shows queue depth and worker status for each group_id.
    Use to diagnose stuck or slow episode processing.
    """
    status = {}
    for group_id, queue in episode_queues.items():
        status[group_id] = {
            "queue_depth": queue.qsize(),
            "worker_alive": queue_workers.get(group_id, False),
        }

    if not status:
        return SuccessResponse(message="No episode queues active")

    lines = []
    for gid, info in status.items():
        state = "running" if info["worker_alive"] else "STOPPED"
        lines.append(f"  {gid}: {info['queue_depth']} queued, worker {state}")

    return SuccessResponse(message="Queue status:\n" + "\n".join(lines))


@mcp.resource('http://graphiti/status')
async def get_status() -> StatusResponse:
    """Get the status of the Graphiti MCP server and Neo4j connection."""
    global graphiti_client

    if graphiti_client is None:
        return StatusResponse(status='error', message='Graphiti client not initialized')

    try:
        # We've already checked that graphiti_client is not None above
        assert graphiti_client is not None

        # Use cast to help the type checker understand that graphiti_client is not None
        client = cast(Graphiti, graphiti_client)

        # Test database connection
        await client.driver.client.verify_connectivity()  # type: ignore

        return StatusResponse(
            status='ok', message='Graphiti MCP server is running and connected to Neo4j'
        )
    except Exception as e:
        error_msg = str(e)
        logger.error(f'Error checking Neo4j connection: {error_msg}')
        return StatusResponse(
            status='error',
            message=f'Graphiti MCP server is running but Neo4j connection failed: {error_msg}',
        )


async def initialize_server() -> MCPConfig:
    """Parse CLI arguments and initialize the Graphiti server configuration."""
    global config

    parser = argparse.ArgumentParser(
        description='Run the Graphiti MCP server with optional LLM client'
    )
    parser.add_argument(
        '--group-id',
        help='Namespace for the graph. This is an arbitrary string used to organize related data. '
        'If not provided, a random UUID will be generated.',
    )
    parser.add_argument(
        '--transport',
        choices=['sse', 'stdio'],
        default='sse',
        help='Transport to use for communication with the client. (default: sse)',
    )
    parser.add_argument(
        '--model', help=f'Model name to use with the LLM client. (default: {DEFAULT_LLM_MODEL})'
    )
    parser.add_argument(
        '--small-model',
        help=f'Small model name to use with the LLM client. (default: {SMALL_LLM_MODEL})',
    )
    parser.add_argument(
        '--temperature',
        type=float,
        help='Temperature setting for the LLM (0.0-2.0). Lower values make output more deterministic. (default: 0.7)',
    )
    parser.add_argument('--destroy-graph', action='store_true', help='Destroy all Graphiti graphs')
    parser.add_argument(
        '--use-custom-entities',
        action='store_true',
        help='Enable entity extraction using the predefined ENTITY_TYPES',
    )
    parser.add_argument(
        '--host',
        default=os.environ.get('MCP_SERVER_HOST'),
        help='Host to bind the MCP server to (default: MCP_SERVER_HOST environment variable)',
    )
    parser.add_argument(
        '--port',
        type=int,
        default=os.environ.get('MCP_SERVER_PORT'),
        help='Port to bind the MCP server to (default: MCP_SERVER_PORT environment variable, or 8000)',
    )

    args = parser.parse_args()

    # Build configuration from CLI arguments and environment variables
    config = GraphitiConfig.from_cli_and_env(args)

    # Log the group ID configuration
    if args.group_id:
        logger.info(f'Using provided group_id: {config.group_id}')
    else:
        logger.info(f'Generated random group_id: {config.group_id}')

    # Log entity extraction configuration
    if config.use_custom_entities:
        logger.info('Entity extraction enabled using predefined ENTITY_TYPES')
    else:
        logger.info('Entity extraction disabled (no custom entities will be used)')

    # Initialize Graphiti
    await initialize_graphiti()

    if args.host:
        logger.info(f'Setting MCP server host to: {args.host}')
        mcp.settings.host = args.host
        # When binding to non-localhost, disable transport security (trusted LAN)
        if args.host not in ('127.0.0.1', 'localhost', '::1'):
            mcp.settings.transport_security = None
    if args.port:
        logger.info(f'Setting MCP server port to: {args.port}')
        mcp.settings.port = args.port

    # Return MCP configuration
    return MCPConfig.from_cli(args)


async def run_mcp_server():
    """Run the MCP server in the current event loop."""
    # Initialize the server
    mcp_config = await initialize_server()

    # Run the server with stdio transport for MCP in the same event loop
    logger.info(f'Starting MCP server with transport: {mcp_config.transport}')
    if mcp_config.transport == 'stdio':
        await mcp.run_stdio_async()
    elif mcp_config.transport == 'sse':
        logger.info(
            f'Running MCP server with SSE transport on {mcp.settings.host}:{mcp.settings.port}'
        )
        await mcp.run_sse_async()


def main():
    """Main function to run the Graphiti MCP server."""
    try:
        # Run everything in a single event loop
        asyncio.run(run_mcp_server())
    except Exception as e:
        logger.error(f'Error initializing Graphiti MCP server: {str(e)}')
        raise


if __name__ == '__main__':
    main()
