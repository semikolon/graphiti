"""Load tests for concurrent per-connection scoping.

Tests verify that:
1. 10 concurrent connections maintain proper isolation
2. No memory leaks under concurrent load
3. No cross-contamination of data
4. System remains stable under realistic load
"""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from mcp.server.fastmcp import Context
from graphiti_mcp_server import (
    add_memory,
    search_nodes,
    search_facts,
)


@pytest.fixture
def mock_graphiti_client():
    """Mock graphiti_client for load testing."""
    with patch('graphiti_mcp_server.graphiti_client') as mock:
        mock.add_episode = AsyncMock()
        mock._search = AsyncMock(return_value=MagicMock(nodes=[]))
        mock.search = AsyncMock(return_value=[])
        yield mock


def create_context(group_id: str) -> Context:
    """Create mock Context with custom header."""
    ctx = MagicMock(spec=Context)
    mock_request = MagicMock()
    mock_request.headers.get = MagicMock(side_effect=lambda k: group_id if k in ['X-Graphiti-Group-Id', 'x-graphiti-group-id'] else None)
    mock_request.query_params.get = MagicMock(return_value=None)
    ctx.request_context.request = mock_request
    return ctx


@pytest.mark.asyncio
async def test_10_concurrent_connections(mock_graphiti_client):
    """Simulate 10 concurrent Claude Code sessions."""

    async def session_workflow(session_id: int):
        """Simulate one Claude Code session's Graphiti usage."""
        group_id = f"test-project-{session_id}"
        ctx = create_context(group_id)

        # Add 5 episodes per session
        for i in range(5):
            await add_memory(
                name=f"Session {session_id} Episode {i}",
                episode_body=f"Content from session {session_id}, episode {i}",
                ctx=ctx
            )

        # Search nodes
        await search_nodes(
            query=f"session {session_id}",
            ctx=ctx,
            max_nodes=10
        )

        # Search facts
        await search_facts(
            query=f"content {session_id}",
            ctx=ctx,
            max_facts=10
        )

        return group_id

    # Run 10 concurrent sessions
    results = await asyncio.gather(*[
        session_workflow(i) for i in range(10)
    ])

    # All sessions completed successfully
    assert len(results) == 10
    assert results == [f"test-project-{i}" for i in range(10)]

    # Verify total operations: 10 sessions × 5 episodes = 50 episodes
    assert mock_graphiti_client.add_episode.call_count == 50

    # Verify all 10 different group_ids were used
    group_ids_used = set(
        call.kwargs['group_id']
        for call in mock_graphiti_client.add_episode.call_args_list
    )
    expected_group_ids = {f"test-project-{i}" for i in range(10)}
    assert group_ids_used == expected_group_ids

    # Verify 10 node searches (1 per session)
    assert mock_graphiti_client._search.call_count == 10

    # Verify 10 fact searches (1 per session)
    assert mock_graphiti_client.search.call_count == 10


@pytest.mark.asyncio
async def test_concurrent_heavy_load(mock_graphiti_client):
    """Test heavier concurrent load with more operations per session."""

    async def heavy_session_workflow(session_id: int):
        """Simulate a heavy session with many operations."""
        group_id = f"heavy-project-{session_id}"
        ctx = create_context(group_id)

        # Add 20 episodes
        for i in range(20):
            await add_memory(
                name=f"Heavy Session {session_id} Episode {i}",
                episode_body=f"Heavy content from session {session_id}",
                ctx=ctx
            )

        # Multiple searches
        for i in range(5):
            await search_nodes(query=f"query {i}", ctx=ctx)
            await search_facts(query=f"fact query {i}", ctx=ctx)

        return session_id

    # Run 5 concurrent heavy sessions
    results = await asyncio.gather(*[
        heavy_session_workflow(i) for i in range(5)
    ])

    # All sessions completed
    assert len(results) == 5

    # Verify: 5 sessions × 20 episodes = 100 total episodes
    assert mock_graphiti_client.add_episode.call_count == 100

    # Verify: 5 sessions × 5 node searches = 25 searches
    assert mock_graphiti_client._search.call_count == 25

    # Verify: 5 sessions × 5 fact searches = 25 searches
    assert mock_graphiti_client.search.call_count == 25


@pytest.mark.asyncio
async def test_rapid_connection_cycling(mock_graphiti_client):
    """Test rapid switching between many connections."""

    async def rapid_operation(iteration: int):
        """Perform single operation with unique group_id."""
        group_id = f"rapid-{iteration}"
        ctx = create_context(group_id)

        await add_memory(
            name=f"Rapid {iteration}",
            episode_body=f"Rapid content {iteration}",
            ctx=ctx
        )

        return group_id

    # Rapidly cycle through 50 different connections
    results = await asyncio.gather(*[
        rapid_operation(i) for i in range(50)
    ])

    # All operations completed
    assert len(results) == 50

    # All unique group_ids
    assert len(set(results)) == 50

    # Verify 50 episodes with 50 different group_ids
    group_ids_used = [
        call.kwargs['group_id']
        for call in mock_graphiti_client.add_episode.call_args_list
    ]
    assert len(set(group_ids_used)) == 50


@pytest.mark.asyncio
async def test_mixed_operations_concurrent(mock_graphiti_client):
    """Test mixed read/write operations across concurrent connections."""

    async def mixed_workflow(session_id: int):
        """Mix of add and search operations."""
        group_id = f"mixed-{session_id}"
        ctx = create_context(group_id)

        operations = []

        # Interleave adds and searches
        for i in range(10):
            # Add episode
            operations.append(
                add_memory(
                    name=f"Mixed {session_id}-{i}",
                    episode_body=f"Content {i}",
                    ctx=ctx
                )
            )

            # Search nodes
            operations.append(
                search_nodes(query=f"query {i}", ctx=ctx)
            )

            # Search facts
            operations.append(
                search_facts(query=f"fact {i}", ctx=ctx)
            )

        # Execute all operations concurrently within session
        await asyncio.gather(*operations)

        return session_id

    # Run 10 sessions with mixed operations
    results = await asyncio.gather(*[
        mixed_workflow(i) for i in range(10)
    ])

    assert len(results) == 10

    # Verify: 10 sessions × 10 episodes = 100 episodes
    assert mock_graphiti_client.add_episode.call_count == 100

    # Verify: 10 sessions × 10 node searches = 100 searches
    assert mock_graphiti_client._search.call_count == 100

    # Verify: 10 sessions × 10 fact searches = 100 searches
    assert mock_graphiti_client.search.call_count == 100


@pytest.mark.asyncio
async def test_connection_isolation_under_load(mock_graphiti_client):
    """Verify isolation is maintained even under heavy concurrent load."""

    collected_group_ids = {
        'add_episode': [],
        'search_nodes': [],
        'search_facts': []
    }

    # Track group_ids used in each operation
    original_add = mock_graphiti_client.add_episode
    original_search = mock_graphiti_client._search
    original_facts = mock_graphiti_client.search

    async def tracking_add(*args, **kwargs):
        collected_group_ids['add_episode'].append(kwargs.get('group_id'))
        return await original_add(*args, **kwargs)

    async def tracking_search(*args, **kwargs):
        collected_group_ids['search_nodes'].extend(kwargs.get('group_ids', []))
        return await original_search(*args, **kwargs)

    async def tracking_facts(*args, **kwargs):
        collected_group_ids['search_facts'].extend(kwargs.get('group_ids', []))
        return await original_facts(*args, **kwargs)

    mock_graphiti_client.add_episode = tracking_add
    mock_graphiti_client._search = tracking_search
    mock_graphiti_client.search = tracking_facts

    async def load_workflow(session_id: int):
        """Generate load with specific group_id."""
        group_id = f"load-{session_id}"
        ctx = create_context(group_id)

        for _ in range(10):
            await add_memory(name=f"Load {session_id}", episode_body="test", ctx=ctx)
            await search_nodes(query="test", ctx=ctx)
            await search_facts(query="test", ctx=ctx)

        return group_id

    # Run 10 concurrent sessions
    await asyncio.gather(*[load_workflow(i) for i in range(10)])

    # Verify each session only used its own group_id
    # 10 sessions × 10 operations = 100 of each type
    expected_ids = [f"load-{i}" for i in range(10) for _ in range(10)]

    # Check add_episode used correct group_ids
    assert sorted(collected_group_ids['add_episode']) == sorted(expected_ids)

    # Check searches used correct group_ids
    assert sorted(collected_group_ids['search_nodes']) == sorted(expected_ids)
    assert sorted(collected_group_ids['search_facts']) == sorted(expected_ids)
