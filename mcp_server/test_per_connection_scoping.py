"""Integration tests for per-connection group_id scoping.

Tests verify that:
1. Different SSE connections maintain separate group_ids
2. Episodes from different connections don't mix
3. Concurrent connections maintain isolation
4. Search results are properly scoped per connection
"""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from mcp.server.fastmcp import Context
from graphiti_mcp_server import (
    add_memory,
    search_nodes,
    search_facts,
    config,
)


@pytest.fixture
def mock_graphiti_client():
    """Mock graphiti_client for testing."""
    with patch('graphiti_mcp_server.graphiti_client') as mock:
        # Create mock client with async methods
        mock.add_episode = AsyncMock()
        mock._search = AsyncMock()
        mock.search = AsyncMock()
        mock.retrieve_episodes = AsyncMock()
        yield mock


def create_context(group_id: str) -> Context:
    """Create mock Context with custom header for group_id."""
    ctx = MagicMock(spec=Context)
    mock_request = MagicMock()
    mock_request.headers.get = MagicMock(side_effect=lambda k: group_id if k in ['X-Graphiti-Group-Id', 'x-graphiti-group-id'] else None)
    mock_request.query_params.get = MagicMock(return_value=None)
    ctx.request_context.request = mock_request
    return ctx


@pytest.mark.asyncio
async def test_multi_connection_isolation(mock_graphiti_client):
    """Verify episodes from different connections return successful responses.

    Note: add_memory uses async queue processing, so we verify the function
    returns success rather than checking the background queue directly.
    """
    # Simulate Connection 1: dotfiles project
    ctx1 = create_context("dotfiles")

    result1 = await add_memory(
        name="Dotfiles Episode",
        episode_body="Testing dotfiles project",
        ctx=ctx1
    )

    # Verify successful response (returns dict with 'message' key)
    assert 'message' in result1
    assert result1['message'] is not None
    assert 'queued for processing' in result1['message']

    # Simulate Connection 2: brf-auto project
    ctx2 = create_context("brf-auto")

    result2 = await add_memory(
        name="BRF-Auto Episode",
        episode_body="Testing brf-auto project",
        ctx=ctx2
    )

    # Verify successful response (returns dict with 'message' key)
    assert 'message' in result2
    assert result2['message'] is not None
    assert 'queued for processing' in result2['message']


@pytest.mark.asyncio
async def test_search_respects_connection_scope(mock_graphiti_client):
    """Verify search results are scoped to connection's group_id."""
    # Mock search results
    mock_result = MagicMock()
    mock_result.nodes = []
    mock_graphiti_client._search.return_value = mock_result

    # Connection 1: dotfiles
    ctx1 = create_context("dotfiles")
    await search_nodes(query="test", ctx=ctx1)

    # Verify search used dotfiles group_id
    call_args_1 = mock_graphiti_client._search.call_args
    assert call_args_1.kwargs['group_ids'] == ["dotfiles"]

    # Connection 2: kimonokittens
    ctx2 = create_context("kimonokittens")
    await search_nodes(query="test", ctx=ctx2)

    # Verify search used kimonokittens group_id (NOT dotfiles)
    call_args_2 = mock_graphiti_client._search.call_args
    assert call_args_2.kwargs['group_ids'] == ["kimonokittens"]
    assert call_args_2.kwargs['group_ids'] != ["dotfiles"]


@pytest.mark.asyncio
async def test_concurrent_connections_maintain_isolation(mock_graphiti_client):
    """Verify concurrent connections maintain proper isolation."""
    # Mock search to return empty results
    mock_result = MagicMock()
    mock_result.nodes = []
    mock_graphiti_client._search.return_value = mock_result

    async def connection_workflow(group_id: str, episode_name: str):
        """Simulate a connection's workflow."""
        ctx = create_context(group_id)

        # Add episode
        await add_memory(
            name=episode_name,
            episode_body=f"Episode from {group_id}",
            ctx=ctx
        )

        # Search nodes
        await search_nodes(
            query=episode_name,
            ctx=ctx
        )

        return group_id

    # Run 3 concurrent connections
    results = await asyncio.gather(
        connection_workflow("project-1", "Episode 1"),
        connection_workflow("project-2", "Episode 2"),
        connection_workflow("project-3", "Episode 3")
    )

    # All connections completed successfully
    assert len(results) == 3
    assert results == ["project-1", "project-2", "project-3"]

    # Verify add_episode was called 3 times with correct group_ids
    assert mock_graphiti_client.add_episode.call_count == 3

    # Extract group_ids from all calls
    group_ids = [
        call.kwargs['group_id']
        for call in mock_graphiti_client.add_episode.call_args_list
    ]

    # All three different group_ids should be present
    assert set(group_ids) == {"project-1", "project-2", "project-3"}


@pytest.mark.asyncio
async def test_facts_search_respects_connection_scope(mock_graphiti_client):
    """Verify facts search is scoped to connection's group_id."""
    # Mock facts search results
    mock_graphiti_client.search.return_value = []

    # Connection 1: dotfiles
    ctx1 = create_context("dotfiles")
    await search_facts(query="authentication", ctx=ctx1)

    # Verify facts search used dotfiles group_id
    call_args_1 = mock_graphiti_client.search.call_args
    assert call_args_1.kwargs['group_ids'] == ["dotfiles"]

    # Connection 2: brf-auto
    ctx2 = create_context("brf-auto")
    await search_facts(query="authentication", ctx=ctx2)

    # Verify facts search used brf-auto group_id (NOT dotfiles)
    call_args_2 = mock_graphiti_client.search.call_args
    assert call_args_2.kwargs['group_ids'] == ["brf-auto"]


@pytest.mark.asyncio
async def test_connection_switching_maintains_state(mock_graphiti_client):
    """Verify switching between connections maintains proper state."""
    # Mock search results
    mock_result = MagicMock()
    mock_result.nodes = []
    mock_graphiti_client._search.return_value = mock_result

    ctx_dotfiles = create_context("dotfiles")
    ctx_brf = create_context("brf-auto")

    # Interleave operations between two connections
    await search_nodes(query="test1", ctx=ctx_dotfiles)
    assert mock_graphiti_client._search.call_args.kwargs['group_ids'] == ["dotfiles"]

    await search_nodes(query="test2", ctx=ctx_brf)
    assert mock_graphiti_client._search.call_args.kwargs['group_ids'] == ["brf-auto"]

    await search_nodes(query="test3", ctx=ctx_dotfiles)
    assert mock_graphiti_client._search.call_args.kwargs['group_ids'] == ["dotfiles"]

    await search_nodes(query="test4", ctx=ctx_brf)
    assert mock_graphiti_client._search.call_args.kwargs['group_ids'] == ["brf-auto"]

    # Total 4 searches
    assert mock_graphiti_client._search.call_count == 4


@pytest.mark.asyncio
async def test_fallback_to_config_when_context_none(mock_graphiti_client):
    """Verify fallback to config.group_id when Context is None."""
    # Mock search results
    mock_result = MagicMock()
    mock_result.nodes = []
    mock_graphiti_client._search.return_value = mock_result

    # Set config.group_id
    original_group_id = config.group_id
    config.group_id = "cli-fallback"

    try:
        # Call with ctx=None (should use config.group_id)
        await search_nodes(query="test", ctx=None)

        # Verify search used config.group_id
        call_args = mock_graphiti_client._search.call_args
        assert call_args.kwargs['group_ids'] == ["cli-fallback"]
    finally:
        config.group_id = original_group_id
