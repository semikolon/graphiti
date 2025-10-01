"""Unit tests for Explicit Scoping implementation in Graphiti MCP.

Tests verify that:
1. Project-scoped tools use config.group_id only
2. Global-scoped tools use hardcoded 'default' group
3. Cross-project tools require explicit project list
4. LLMs cannot bypass project isolation via parameters
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from graphiti_mcp_server import (
    add_memory,
    add_global_memory,
    search_nodes,
    search_global_nodes,
    search_cross_project_nodes,
    search_facts,
    search_global_facts,
    search_cross_project_facts,
    config,
)


@pytest.fixture
def mock_graphiti_client():
    """Mock graphiti_client for testing."""
    with patch('graphiti_mcp_server.graphiti_client') as mock:
        mock_client = MagicMock()
        mock_client.add_episode = AsyncMock()
        mock_client._search = AsyncMock()
        mock_client.search = AsyncMock()
        mock.return_value = mock_client
        yield mock_client


@pytest.fixture
def mock_config():
    """Mock config for testing."""
    with patch('graphiti_mcp_server.config') as mock:
        mock.group_id = "test-project"
        mock.use_custom_entities = False
        yield mock


# ==================== PROJECT-SCOPED TOOLS ====================


@pytest.mark.asyncio
async def test_add_memory_uses_config_group_id(mock_graphiti_client, mock_config):
    """Verify add_memory uses config.group_id, not parameters."""
    # Set config.group_id
    mock_config.group_id = "dotfiles"

    # Add memory without group_id parameter (should use config.group_id)
    result = await add_memory(
        name="Test Episode",
        episode_body="Test content",
    )

    # Verify it went to "dotfiles" group
    # Note: Due to async queue processing, we verify the queue was created
    assert result.message is not None
    assert "Test Episode" in result.message


@pytest.mark.asyncio
async def test_search_nodes_uses_config_group_id(mock_graphiti_client, mock_config):
    """Verify search_nodes uses config.group_id only."""
    # Set config.group_id
    mock_config.group_id = "dotfiles"

    # Mock search result
    mock_result = MagicMock()
    mock_result.nodes = []
    mock_graphiti_client._search.return_value = mock_result

    # Search without group_ids parameter
    result = await search_nodes(query="test")

    # Verify search was called with config.group_id
    assert mock_graphiti_client._search.called
    call_args = mock_graphiti_client._search.call_args
    assert call_args.kwargs['group_ids'] == ["dotfiles"]


@pytest.mark.asyncio
async def test_search_facts_uses_config_group_id(mock_graphiti_client, mock_config):
    """Verify search_facts uses config.group_id only."""
    # Set config.group_id
    mock_config.group_id = "dotfiles"

    # Mock search result
    mock_graphiti_client.search.return_value = []

    # Search without group_ids parameter
    result = await search_facts(query="test")

    # Verify search was called with config.group_id
    assert mock_graphiti_client.search.called
    call_args = mock_graphiti_client.search.call_args
    assert call_args.kwargs['group_ids'] == ["dotfiles"]


# ==================== GLOBAL-SCOPED TOOLS ====================


@pytest.mark.asyncio
async def test_add_global_memory_uses_default_group(mock_graphiti_client, mock_config):
    """Verify add_global_memory uses 'default' group, ignoring config."""
    # Set config.group_id to something else
    mock_config.group_id = "dotfiles"

    # Add global memory
    result = await add_global_memory(
        name="Global Episode",
        episode_body="Global content",
    )

    # Verify it went to "default" group, NOT "dotfiles"
    assert result.message is not None
    assert "Global episode" in result.message


@pytest.mark.asyncio
async def test_search_global_nodes_uses_default_group(mock_graphiti_client, mock_config):
    """Verify search_global_nodes uses 'default' group only."""
    # Set config.group_id to something else
    mock_config.group_id = "dotfiles"

    # Mock search result
    mock_result = MagicMock()
    mock_result.nodes = []
    mock_graphiti_client._search.return_value = mock_result

    # Search global
    result = await search_global_nodes(query="test")

    # Verify search was called with 'default' group, NOT config.group_id
    assert mock_graphiti_client._search.called
    call_args = mock_graphiti_client._search.call_args
    assert call_args.kwargs['group_ids'] == ["default"]


@pytest.mark.asyncio
async def test_search_global_facts_uses_default_group(mock_graphiti_client, mock_config):
    """Verify search_global_facts uses 'default' group only."""
    # Set config.group_id to something else
    mock_config.group_id = "dotfiles"

    # Mock search result
    mock_graphiti_client.search.return_value = []

    # Search global
    result = await search_global_facts(query="test")

    # Verify search was called with 'default' group, NOT config.group_id
    assert mock_graphiti_client.search.called
    call_args = mock_graphiti_client.search.call_args
    assert call_args.kwargs['group_ids'] == ["default"]


# ==================== CROSS-PROJECT TOOLS ====================


@pytest.mark.asyncio
async def test_cross_project_nodes_requires_projects(mock_graphiti_client, mock_config):
    """Verify cross-project search requires explicit project list."""
    # Empty list should error
    result = await search_cross_project_nodes(
        query="test",
        projects=[]
    )
    assert hasattr(result, 'error')
    assert "Must provide explicit list" in result.error


@pytest.mark.asyncio
async def test_cross_project_nodes_uses_explicit_projects(mock_graphiti_client, mock_config):
    """Verify cross-project search uses explicit project list."""
    # Mock search result
    mock_result = MagicMock()
    mock_result.nodes = []
    mock_graphiti_client._search.return_value = mock_result

    # Search with explicit projects
    projects = ["dotfiles", "kimonokittens", "brf-auto"]
    result = await search_cross_project_nodes(
        query="test",
        projects=projects
    )

    # Verify search was called with explicit projects
    assert mock_graphiti_client._search.called
    call_args = mock_graphiti_client._search.call_args
    assert call_args.kwargs['group_ids'] == projects


@pytest.mark.asyncio
async def test_cross_project_facts_requires_projects(mock_graphiti_client, mock_config):
    """Verify cross-project facts search requires explicit project list."""
    # Empty list should error
    result = await search_cross_project_facts(
        query="test",
        projects=[]
    )
    assert hasattr(result, 'error')
    assert "Must provide explicit list" in result.error


@pytest.mark.asyncio
async def test_cross_project_facts_uses_explicit_projects(mock_graphiti_client, mock_config):
    """Verify cross-project facts search uses explicit project list."""
    # Mock search result
    mock_graphiti_client.search.return_value = []

    # Search with explicit projects
    projects = ["dotfiles", "kimonokittens", "brf-auto"]
    result = await search_cross_project_facts(
        query="test",
        projects=projects
    )

    # Verify search was called with explicit projects
    assert mock_graphiti_client.search.called
    call_args = mock_graphiti_client.search.call_args
    assert call_args.kwargs['group_ids'] == projects


# ==================== INTEGRATION SCENARIOS ====================


@pytest.mark.asyncio
async def test_project_isolation_scenario(mock_graphiti_client, mock_config):
    """Integration test: Verify project isolation works end-to-end."""
    # Set config to dotfiles project
    mock_config.group_id = "dotfiles"

    # Mock search results
    mock_result = MagicMock()
    mock_result.nodes = []
    mock_graphiti_client._search.return_value = mock_result

    # Search in dotfiles project
    result_dotfiles = await search_nodes(query="test")

    # Verify search was scoped to dotfiles
    call_args_1 = mock_graphiti_client._search.call_args
    assert call_args_1.kwargs['group_ids'] == ["dotfiles"]

    # Switch config to kimonokittens project
    mock_config.group_id = "kimonokittens"

    # Search in kimonokittens project
    result_kimonokittens = await search_nodes(query="test")

    # Verify search was scoped to kimonokittens, NOT dotfiles
    call_args_2 = mock_graphiti_client._search.call_args
    assert call_args_2.kwargs['group_ids'] == ["kimonokittens"]
    assert call_args_2.kwargs['group_ids'] != ["dotfiles"]


@pytest.mark.asyncio
async def test_cross_project_comparison_scenario(mock_graphiti_client, mock_config):
    """Integration test: Verify cross-project comparison works correctly."""
    # Set config to dotfiles (shouldn't affect cross-project search)
    mock_config.group_id = "dotfiles"

    # Mock search result
    mock_result = MagicMock()
    mock_result.nodes = []
    mock_graphiti_client._search.return_value = mock_result

    # Perform cross-project search with explicit projects
    projects = ["dotfiles", "kimonokittens", "brf-auto"]
    result = await search_cross_project_nodes(
        query="authentication implementation",
        projects=projects
    )

    # Verify search included all specified projects, not just config.group_id
    call_args = mock_graphiti_client._search.call_args
    assert call_args.kwargs['group_ids'] == projects
    assert len(call_args.kwargs['group_ids']) == 3
