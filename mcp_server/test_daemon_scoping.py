"""Tests for shared daemon mode: per-connection group_id scoping via driver.clone.

Verifies that the 5 fixed tools (raw_cypher_query, delete_entity_edge, delete_episode,
get_entity_edge, clear_graph) correctly derive group_id from the request context and
clone the driver to target the correct FalkorDB graph.

Tests verify:
1. Each tool calls get_effective_group_id(ctx) to determine the session's group_id
2. Each tool calls driver.clone(database=group_id) to get a scoped driver
3. Different sessions with different group_ids don't cross-contaminate
4. Operations use the cloned driver, not the default client.driver
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from mcp.server.fastmcp import Context


def create_context(group_id: str) -> Context:
    """Create mock Context with custom header for group_id."""
    ctx = MagicMock(spec=Context)
    mock_request = MagicMock()
    mock_request.headers.get = MagicMock(
        side_effect=lambda k: group_id if k in ['X-Graphiti-Group-Id', 'x-graphiti-group-id'] else None
    )
    mock_request.query_params.get = MagicMock(return_value=None)
    ctx.request_context.request = mock_request
    return ctx


def is_error_response(result) -> bool:
    """Check if result is an error response (TypedDict with 'error' key)."""
    return isinstance(result, dict) and 'error' in result


@pytest.fixture
def mock_graphiti_with_clone():
    """Mock graphiti_client with driver.clone support.

    Returns separate cloned drivers per group_id to verify isolation.
    """
    mock_client = MagicMock()
    mock_default_driver = MagicMock()

    # Track cloned drivers by group_id
    cloned_drivers = {}

    def clone_driver(database):
        if database not in cloned_drivers:
            cloned = MagicMock()
            cloned.execute_query = AsyncMock()
            cloned._database = database
            cloned_drivers[database] = cloned
        return cloned_drivers[database]

    mock_default_driver.clone = MagicMock(side_effect=clone_driver)
    mock_default_driver.execute_query = AsyncMock()
    mock_default_driver._database = 'startup-default'
    mock_client.driver = mock_default_driver
    mock_client.build_indices_and_constraints = AsyncMock()

    with patch('graphiti_mcp_server.graphiti_client', mock_client):
        yield mock_client, mock_default_driver, cloned_drivers


# ==================== raw_cypher_query ====================


@pytest.mark.asyncio
async def test_raw_cypher_clones_driver_for_group_id(mock_graphiti_with_clone):
    """raw_cypher_query should clone the driver with the session's group_id."""
    mock_client, mock_default_driver, cloned_drivers = mock_graphiti_with_clone

    ctx = create_context("dotfiles")
    cloned_drivers["dotfiles"] = MagicMock()
    cloned_drivers["dotfiles"].execute_query = AsyncMock(return_value=(
        [{'name': 'test'}], ['name'], None
    ))
    mock_default_driver.clone = MagicMock(side_effect=lambda database: cloned_drivers.get(database, MagicMock()))

    from graphiti_mcp_server import raw_cypher_query

    result = await raw_cypher_query(
        query="MATCH (n) RETURN n LIMIT 5",
        ctx=ctx,
    )

    # Verify clone was called with the correct group_id
    mock_default_driver.clone.assert_called_with(database="dotfiles")

    # Verify execute_query was called on the cloned driver, NOT the default
    cloned_drivers["dotfiles"].execute_query.assert_called_once()
    mock_default_driver.execute_query.assert_not_called()


@pytest.mark.asyncio
async def test_raw_cypher_different_sessions_use_different_drivers(mock_graphiti_with_clone):
    """Two sessions with different group_ids should get different cloned drivers."""
    mock_client, mock_default_driver, cloned_drivers = mock_graphiti_with_clone

    # Pre-create cloned drivers
    for gid in ["project-a", "project-b"]:
        d = MagicMock()
        d.execute_query = AsyncMock(return_value=([{'g': gid}], ['g'], None))
        cloned_drivers[gid] = d
    mock_default_driver.clone = MagicMock(side_effect=lambda database: cloned_drivers[database])

    from graphiti_mcp_server import raw_cypher_query

    ctx_a = create_context("project-a")
    ctx_b = create_context("project-b")

    await raw_cypher_query(query="MATCH (n) RETURN n LIMIT 1", ctx=ctx_a)
    await raw_cypher_query(query="MATCH (n) RETURN n LIMIT 1", ctx=ctx_b)

    # Each session used its own cloned driver
    cloned_drivers["project-a"].execute_query.assert_called_once()
    cloned_drivers["project-b"].execute_query.assert_called_once()


# ==================== delete_entity_edge ====================


@pytest.mark.asyncio
async def test_delete_entity_edge_clones_driver(mock_graphiti_with_clone):
    """delete_entity_edge should use cloned driver for the session's group_id."""
    mock_client, mock_default_driver, cloned_drivers = mock_graphiti_with_clone

    cloned = MagicMock()
    cloned_drivers["brf-auto"] = cloned
    mock_default_driver.clone = MagicMock(return_value=cloned)

    mock_edge = MagicMock()
    mock_edge.delete = AsyncMock()

    ctx = create_context("brf-auto")

    with patch('graphiti_mcp_server.EntityEdge') as MockEntityEdge:
        MockEntityEdge.get_by_uuid = AsyncMock(return_value=mock_edge)

        from graphiti_mcp_server import delete_entity_edge
        result = await delete_entity_edge(uuid="test-uuid-123", ctx=ctx)

    # Verify clone called with correct group_id
    mock_default_driver.clone.assert_called_with(database="brf-auto")

    # Verify get_by_uuid used the cloned driver
    MockEntityEdge.get_by_uuid.assert_called_once_with(cloned, "test-uuid-123")

    # Verify delete used the cloned driver
    mock_edge.delete.assert_called_once_with(cloned)

    assert 'message' in result
    assert 'deleted successfully' in result['message']


@pytest.mark.asyncio
async def test_delete_entity_edge_isolation(mock_graphiti_with_clone):
    """delete_entity_edge from different sessions should use different drivers."""
    mock_client, mock_default_driver, cloned_drivers = mock_graphiti_with_clone

    cloned_a = MagicMock()
    cloned_b = MagicMock()
    cloned_drivers["proj-a"] = cloned_a
    cloned_drivers["proj-b"] = cloned_b
    mock_default_driver.clone = MagicMock(side_effect=lambda database: cloned_drivers[database])

    mock_edge = MagicMock()
    mock_edge.delete = AsyncMock()

    with patch('graphiti_mcp_server.EntityEdge') as MockEntityEdge:
        MockEntityEdge.get_by_uuid = AsyncMock(return_value=mock_edge)

        from graphiti_mcp_server import delete_entity_edge

        await delete_entity_edge(uuid="uuid-1", ctx=create_context("proj-a"))
        await delete_entity_edge(uuid="uuid-2", ctx=create_context("proj-b"))

    # Verify each used its own driver
    clone_calls = mock_default_driver.clone.call_args_list
    assert call(database="proj-a") in clone_calls
    assert call(database="proj-b") in clone_calls


# ==================== delete_episode ====================


@pytest.mark.asyncio
async def test_delete_episode_clones_driver(mock_graphiti_with_clone):
    """delete_episode should use cloned driver for the session's group_id."""
    mock_client, mock_default_driver, cloned_drivers = mock_graphiti_with_clone

    cloned = MagicMock()
    cloned_drivers["kimonokittens"] = cloned
    mock_default_driver.clone = MagicMock(return_value=cloned)

    mock_episode = MagicMock()
    mock_episode.delete = AsyncMock()

    ctx = create_context("kimonokittens")

    with patch('graphiti_mcp_server.EpisodicNode') as MockEpisodicNode:
        MockEpisodicNode.get_by_uuid = AsyncMock(return_value=mock_episode)

        from graphiti_mcp_server import delete_episode
        result = await delete_episode(uuid="episode-uuid-456", ctx=ctx)

    # Verify clone called with correct group_id
    mock_default_driver.clone.assert_called_with(database="kimonokittens")

    # Verify get_by_uuid used the cloned driver
    MockEpisodicNode.get_by_uuid.assert_called_once_with(cloned, "episode-uuid-456")

    # Verify delete used the cloned driver
    mock_episode.delete.assert_called_once_with(cloned)

    assert 'message' in result
    assert 'deleted successfully' in result['message']


# ==================== get_entity_edge ====================


@pytest.mark.asyncio
async def test_get_entity_edge_clones_driver(mock_graphiti_with_clone):
    """get_entity_edge should use cloned driver for the session's group_id."""
    mock_client, mock_default_driver, cloned_drivers = mock_graphiti_with_clone

    cloned = MagicMock()
    cloned_drivers["sluss"] = cloned
    mock_default_driver.clone = MagicMock(return_value=cloned)

    mock_edge = MagicMock()
    mock_edge.uuid = "edge-uuid-789"
    mock_edge.fact = "test fact"
    mock_edge.source_node_uuid = "src-uuid"
    mock_edge.target_node_uuid = "tgt-uuid"
    mock_edge.source_node_name = "Source"
    mock_edge.target_node_name = "Target"
    mock_edge.group_id = "sluss"
    mock_edge.created_at = MagicMock()
    mock_edge.created_at.isoformat = MagicMock(return_value="2026-01-01T00:00:00")
    mock_edge.episodes = []
    mock_edge.expired_at = None

    ctx = create_context("sluss")

    with patch('graphiti_mcp_server.EntityEdge') as MockEntityEdge:
        MockEntityEdge.get_by_uuid = AsyncMock(return_value=mock_edge)

        from graphiti_mcp_server import get_entity_edge
        result = await get_entity_edge(uuid="edge-uuid-789", ctx=ctx)

    # Verify clone called with correct group_id
    mock_default_driver.clone.assert_called_with(database="sluss")

    # Verify get_by_uuid used the cloned driver
    MockEntityEdge.get_by_uuid.assert_called_once_with(cloned, "edge-uuid-789")

    # Should return formatted result, not error
    assert not is_error_response(result)


@pytest.mark.asyncio
async def test_get_entity_edge_isolation(mock_graphiti_with_clone):
    """get_entity_edge from different sessions should use different drivers."""
    mock_client, mock_default_driver, cloned_drivers = mock_graphiti_with_clone

    cloned_x = MagicMock()
    cloned_y = MagicMock()
    cloned_drivers["proj-x"] = cloned_x
    cloned_drivers["proj-y"] = cloned_y
    mock_default_driver.clone = MagicMock(side_effect=lambda database: cloned_drivers[database])

    mock_edge = MagicMock()
    mock_edge.uuid = "e"
    mock_edge.fact = "f"
    mock_edge.source_node_uuid = "s"
    mock_edge.target_node_uuid = "t"
    mock_edge.source_node_name = "S"
    mock_edge.target_node_name = "T"
    mock_edge.group_id = "g"
    mock_edge.created_at = MagicMock()
    mock_edge.created_at.isoformat = MagicMock(return_value="2026-01-01")
    mock_edge.episodes = []
    mock_edge.expired_at = None

    with patch('graphiti_mcp_server.EntityEdge') as MockEntityEdge:
        MockEntityEdge.get_by_uuid = AsyncMock(return_value=mock_edge)

        from graphiti_mcp_server import get_entity_edge

        await get_entity_edge(uuid="e1", ctx=create_context("proj-x"))
        await get_entity_edge(uuid="e2", ctx=create_context("proj-y"))

    clone_calls = mock_default_driver.clone.call_args_list
    assert call(database="proj-x") in clone_calls
    assert call(database="proj-y") in clone_calls


# ==================== clear_graph ====================


@pytest.mark.asyncio
async def test_clear_graph_clones_driver(mock_graphiti_with_clone):
    """clear_graph should clear only the requesting session's graph."""
    mock_client, mock_default_driver, cloned_drivers = mock_graphiti_with_clone

    cloned = MagicMock()
    cloned_drivers["test-project"] = cloned
    mock_default_driver.clone = MagicMock(return_value=cloned)

    ctx = create_context("test-project")

    with patch('graphiti_mcp_server.clear_data', new_callable=AsyncMock) as mock_clear_data, \
         patch('graphiti_mcp_server.build_indices_and_constraints', new_callable=AsyncMock) as mock_build:
        from graphiti_mcp_server import clear_graph
        result = await clear_graph(ctx=ctx)

    # Verify clone called with correct group_id
    mock_default_driver.clone.assert_called_with(database="test-project")

    # Verify clear_data used the cloned driver, NOT the default
    mock_clear_data.assert_called_once_with(cloned)

    # Verify indices rebuilt on the cloned driver, NOT the default
    mock_build.assert_called_once_with(cloned)

    assert 'message' in result
    assert 'test-project' in result['message']
    assert 'cleared successfully' in result['message']


@pytest.mark.asyncio
async def test_clear_graph_does_not_clear_other_projects(mock_graphiti_with_clone):
    """Clearing project-a's graph should NOT affect project-b."""
    mock_client, mock_default_driver, cloned_drivers = mock_graphiti_with_clone

    cloned_a = MagicMock()
    cloned_b = MagicMock()
    cloned_drivers["project-a"] = cloned_a
    cloned_drivers["project-b"] = cloned_b
    mock_default_driver.clone = MagicMock(side_effect=lambda database: cloned_drivers[database])

    ctx_a = create_context("project-a")

    with patch('graphiti_mcp_server.clear_data', new_callable=AsyncMock) as mock_clear_data, \
         patch('graphiti_mcp_server.build_indices_and_constraints', new_callable=AsyncMock) as mock_build:
        from graphiti_mcp_server import clear_graph
        await clear_graph(ctx=ctx_a)

    # clear_data should only be called with project-a's driver
    mock_clear_data.assert_called_once_with(cloned_a)

    # indices should only be rebuilt on project-a's driver
    mock_build.assert_called_once_with(cloned_a)

    # project-b's driver should never have been created or used
    assert "project-b" not in [c.kwargs.get('database') for c in mock_default_driver.clone.call_args_list]


# ==================== CROSS-TOOL ISOLATION ====================


@pytest.mark.asyncio
async def test_concurrent_sessions_full_isolation(mock_graphiti_with_clone):
    """Simulate two concurrent sessions using different tools — verify complete isolation."""
    mock_client, mock_default_driver, cloned_drivers = mock_graphiti_with_clone

    # Pre-create cloned drivers for each session
    for gid in ["session-1-project", "session-2-project"]:
        d = MagicMock()
        d.execute_query = AsyncMock(return_value=([{'g': gid}], ['g'], None))
        d._database = gid
        cloned_drivers[gid] = d
    mock_default_driver.clone = MagicMock(side_effect=lambda database: cloned_drivers[database])

    mock_edge = MagicMock()
    mock_edge.uuid = "e"
    mock_edge.fact = "f"
    mock_edge.source_node_uuid = "s"
    mock_edge.target_node_uuid = "t"
    mock_edge.source_node_name = "S"
    mock_edge.target_node_name = "T"
    mock_edge.group_id = "g"
    mock_edge.created_at = MagicMock()
    mock_edge.created_at.isoformat = MagicMock(return_value="2026-01-01")
    mock_edge.episodes = []
    mock_edge.expired_at = None

    ctx1 = create_context("session-1-project")
    ctx2 = create_context("session-2-project")

    from graphiti_mcp_server import raw_cypher_query, get_entity_edge

    with patch('graphiti_mcp_server.EntityEdge') as MockEntityEdge:
        MockEntityEdge.get_by_uuid = AsyncMock(return_value=mock_edge)

        # Session 1 runs a cypher query
        await raw_cypher_query(query="MATCH (n) RETURN n LIMIT 1", ctx=ctx1)

        # Session 2 gets an entity edge
        await get_entity_edge(uuid="some-uuid", ctx=ctx2)

    # Session 1's cypher query used session-1's driver
    cloned_drivers["session-1-project"].execute_query.assert_called_once()

    # Session 2's get_entity_edge used session-2's driver
    MockEntityEdge.get_by_uuid.assert_called_once_with(
        cloned_drivers["session-2-project"], "some-uuid"
    )

    # Neither session touched the default driver
    mock_default_driver.execute_query.assert_not_called()
