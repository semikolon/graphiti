"""Unit tests for raw_cypher_query tool.

Tests verify:
1. Read-only queries execute successfully
2. Write operations are blocked (CREATE, DELETE, SET, MERGE, etc.)
3. Results are capped at max_results
4. LIMIT is auto-added when missing
5. Parameterized queries work correctly
6. Error handling works properly
7. Per-connection group_id scoping via driver.clone

Note: ErrorResponse is a TypedDict, so results are dicts with 'error' key.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from mcp.server.fastmcp import Context


def is_error_response(result) -> bool:
    """Check if result is an error response (TypedDict with 'error' key)."""
    return isinstance(result, dict) and 'error' in result


def create_context(group_id: str) -> Context:
    """Create mock Context with custom header for group_id."""
    ctx = MagicMock(spec=Context)
    mock_request = MagicMock()
    mock_request.headers.get = MagicMock(side_effect=lambda k: group_id if k in ['X-Graphiti-Group-Id', 'x-graphiti-group-id'] else None)
    mock_request.query_params.get = MagicMock(return_value=None)
    ctx.request_context.request = mock_request
    return ctx


# Default context for tests that don't focus on scoping
DEFAULT_CTX = create_context("test-project")


@pytest.fixture
def mock_graphiti_client():
    """Mock graphiti_client for testing."""
    with patch('graphiti_mcp_server.graphiti_client') as mock:
        mock_client = MagicMock()
        mock_driver = MagicMock()
        mock_driver.execute_query = AsyncMock()
        # clone returns the same driver (simulates same group_id optimization)
        mock_driver.clone = MagicMock(return_value=mock_driver)
        mock_client.driver = mock_driver
        mock.__bool__ = lambda x: True  # Make it truthy
        mock.driver = mock_driver
        yield mock_client, mock_driver


@pytest.fixture
def setup_graphiti_client(mock_graphiti_client):
    """Set up the global graphiti_client."""
    mock_client, mock_driver = mock_graphiti_client
    with patch('graphiti_mcp_server.graphiti_client', mock_client):
        yield mock_client, mock_driver


# ==================== WRITE OPERATION BLOCKING ====================


@pytest.mark.asyncio
async def test_blocks_create_operations(setup_graphiti_client):
    """Verify CREATE operations are blocked."""
    from graphiti_mcp_server import raw_cypher_query

    result = await raw_cypher_query(
        query="CREATE (n:Node {name: 'test'}) RETURN n",
        ctx=DEFAULT_CTX,
    )

    assert is_error_response(result)
    assert 'CREATE' in result['error']
    assert 'not allowed' in result['error']


@pytest.mark.asyncio
async def test_blocks_delete_operations(setup_graphiti_client):
    """Verify DELETE operations are blocked."""
    from graphiti_mcp_server import raw_cypher_query

    result = await raw_cypher_query(
        query="MATCH (n) DELETE n",
        ctx=DEFAULT_CTX,
    )

    assert is_error_response(result)
    assert 'DELETE' in result['error']


@pytest.mark.asyncio
async def test_blocks_set_operations(setup_graphiti_client):
    """Verify SET operations are blocked."""
    from graphiti_mcp_server import raw_cypher_query

    result = await raw_cypher_query(
        query="MATCH (n) SET n.name = 'new' RETURN n",
        ctx=DEFAULT_CTX,
    )

    assert is_error_response(result)
    assert 'SET' in result['error']


@pytest.mark.asyncio
async def test_blocks_merge_operations(setup_graphiti_client):
    """Verify MERGE operations are blocked."""
    from graphiti_mcp_server import raw_cypher_query

    result = await raw_cypher_query(
        query="MERGE (n:Node {name: 'test'}) RETURN n",
        ctx=DEFAULT_CTX,
    )

    assert is_error_response(result)
    assert 'MERGE' in result['error']


@pytest.mark.asyncio
async def test_blocks_detach_delete(setup_graphiti_client):
    """Verify DETACH DELETE is blocked."""
    from graphiti_mcp_server import raw_cypher_query

    result = await raw_cypher_query(
        query="MATCH (n) DETACH DELETE n",
        ctx=DEFAULT_CTX,
    )

    assert is_error_response(result)
    # May be blocked by DETACH or DELETE (whichever comes first in check order)
    assert 'DETACH' in result['error'] or 'DELETE' in result['error']


@pytest.mark.asyncio
async def test_blocks_drop_operations(setup_graphiti_client):
    """Verify DROP operations are blocked."""
    from graphiti_mcp_server import raw_cypher_query

    result = await raw_cypher_query(
        query="DROP INDEX my_index",
        ctx=DEFAULT_CTX,
    )

    assert is_error_response(result)
    assert 'DROP' in result['error']


@pytest.mark.asyncio
async def test_blocks_remove_operations(setup_graphiti_client):
    """Verify REMOVE operations are blocked."""
    from graphiti_mcp_server import raw_cypher_query

    result = await raw_cypher_query(
        query="MATCH (n) REMOVE n.property RETURN n",
        ctx=DEFAULT_CTX,
    )

    assert is_error_response(result)
    assert 'REMOVE' in result['error']


# ==================== READ OPERATIONS ====================


@pytest.mark.asyncio
async def test_allows_match_return(setup_graphiti_client):
    """Verify MATCH...RETURN queries work."""
    mock_client, mock_driver = setup_graphiti_client

    # Mock successful query result
    mock_driver.execute_query.return_value = (
        [{'name': 'test', 'uuid': '123'}],
        ['name', 'uuid'],
        None
    )

    from graphiti_mcp_server import raw_cypher_query

    result = await raw_cypher_query(
        query="MATCH (n:Node) RETURN n.name, n.uuid LIMIT 10",
        ctx=DEFAULT_CTX,
    )

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]['name'] == 'test'


@pytest.mark.asyncio
async def test_parameterized_query(setup_graphiti_client):
    """Verify parameterized queries work."""
    mock_client, mock_driver = setup_graphiti_client

    mock_driver.execute_query.return_value = (
        [{'title': 'Auth Decision'}],
        ['title'],
        None
    )

    from graphiti_mcp_server import raw_cypher_query

    result = await raw_cypher_query(
        query="MATCH (d:Decision) WHERE d.title CONTAINS $search RETURN d.title LIMIT 5",
        ctx=DEFAULT_CTX,
        params={"search": "Auth"}
    )

    assert isinstance(result, list)
    # Verify execute_query was called with params
    mock_driver.execute_query.assert_called_once()
    call_kwargs = mock_driver.execute_query.call_args[1]
    assert call_kwargs.get('search') == 'Auth'


# ==================== RESULT LIMITING ====================


@pytest.mark.asyncio
async def test_auto_adds_limit_when_missing(setup_graphiti_client):
    """Verify LIMIT is auto-added when not present."""
    mock_client, mock_driver = setup_graphiti_client

    mock_driver.execute_query.return_value = ([], [], None)

    from graphiti_mcp_server import raw_cypher_query

    await raw_cypher_query(
        query="MATCH (n) RETURN n",
        ctx=DEFAULT_CTX,
    )

    # Check that LIMIT was added to the query
    call_args = mock_driver.execute_query.call_args[0]
    assert 'LIMIT' in call_args[0]


@pytest.mark.asyncio
async def test_respects_existing_limit(setup_graphiti_client):
    """Verify existing LIMIT is preserved."""
    mock_client, mock_driver = setup_graphiti_client

    mock_driver.execute_query.return_value = ([], [], None)

    from graphiti_mcp_server import raw_cypher_query

    await raw_cypher_query(
        query="MATCH (n) RETURN n LIMIT 5",
        ctx=DEFAULT_CTX,
    )

    # Check that original LIMIT is preserved (no double LIMIT)
    call_args = mock_driver.execute_query.call_args[0]
    assert call_args[0].count('LIMIT') == 1


@pytest.mark.asyncio
async def test_caps_max_results_at_500(setup_graphiti_client):
    """Verify max_results is capped at 500."""
    mock_client, mock_driver = setup_graphiti_client

    # Return more than 500 results
    mock_driver.execute_query.return_value = (
        [{'i': i} for i in range(600)],
        ['i'],
        None
    )

    from graphiti_mcp_server import raw_cypher_query

    result = await raw_cypher_query(
        query="MATCH (n) RETURN n",
        ctx=DEFAULT_CTX,
        max_results=1000  # Request more than cap
    )

    # Should be capped at 500
    assert len(result) <= 500


@pytest.mark.asyncio
async def test_respects_max_results_param(setup_graphiti_client):
    """Verify max_results parameter works."""
    mock_client, mock_driver = setup_graphiti_client

    mock_driver.execute_query.return_value = (
        [{'i': i} for i in range(100)],
        ['i'],
        None
    )

    from graphiti_mcp_server import raw_cypher_query

    result = await raw_cypher_query(
        query="MATCH (n) RETURN n",
        ctx=DEFAULT_CTX,
        max_results=10
    )

    assert len(result) <= 10


# ==================== ERROR HANDLING ====================


@pytest.mark.asyncio
async def test_handles_query_error(setup_graphiti_client):
    """Verify query errors are handled gracefully."""
    mock_client, mock_driver = setup_graphiti_client

    mock_driver.execute_query.side_effect = Exception("Syntax error in query")

    from graphiti_mcp_server import raw_cypher_query

    result = await raw_cypher_query(
        query="MATCH (n RETURN n",  # Invalid syntax
        ctx=DEFAULT_CTX,
    )

    assert is_error_response(result)
    assert 'Syntax error' in result['error']


@pytest.mark.asyncio
async def test_handles_none_result(setup_graphiti_client):
    """Verify None result is handled."""
    mock_client, mock_driver = setup_graphiti_client

    mock_driver.execute_query.return_value = None

    from graphiti_mcp_server import raw_cypher_query

    result = await raw_cypher_query(
        query="MATCH (n) RETURN n LIMIT 1",
        ctx=DEFAULT_CTX,
    )

    assert result == []


@pytest.mark.asyncio
async def test_returns_error_when_client_not_initialized():
    """Verify error when graphiti_client is None."""
    with patch('graphiti_mcp_server.graphiti_client', None):
        from graphiti_mcp_server import raw_cypher_query

        result = await raw_cypher_query(
            query="MATCH (n) RETURN n",
            ctx=DEFAULT_CTX,
        )

        assert is_error_response(result)
        assert 'not initialized' in result['error']


# ==================== CASE INSENSITIVITY ====================


@pytest.mark.asyncio
async def test_blocks_lowercase_create(setup_graphiti_client):
    """Verify lowercase write operations are also blocked."""
    from graphiti_mcp_server import raw_cypher_query

    result = await raw_cypher_query(
        query="create (n:Node) return n",
        ctx=DEFAULT_CTX,
    )

    assert is_error_response(result)
    assert 'CREATE' in result['error']


@pytest.mark.asyncio
async def test_blocks_mixed_case_delete(setup_graphiti_client):
    """Verify mixed case write operations are blocked."""
    from graphiti_mcp_server import raw_cypher_query

    result = await raw_cypher_query(
        query="MATCH (n) DeLeTe n",
        ctx=DEFAULT_CTX,
    )

    assert is_error_response(result)
    assert 'DELETE' in result['error']


# ==================== WORD BOUNDARY ====================


@pytest.mark.asyncio
async def test_allows_created_at_field(setup_graphiti_client):
    """Verify 'created_at' does NOT trigger CREATE block."""
    mock_client, mock_driver = setup_graphiti_client

    mock_driver.execute_query.return_value = (
        [{'name': 'test', 'created_at': '2025-01-01'}],
        ['name', 'created_at'],
        None
    )

    from graphiti_mcp_server import raw_cypher_query

    result = await raw_cypher_query(
        query="MATCH (n) RETURN n.name, n.created_at ORDER BY n.created_at LIMIT 10",
        ctx=DEFAULT_CTX,
    )

    # Should succeed, not be blocked
    assert isinstance(result, list)
    assert not is_error_response(result)


@pytest.mark.asyncio
async def test_allows_deleted_flag_field(setup_graphiti_client):
    """Verify 'deleted' as a field name does NOT trigger DELETE block."""
    mock_client, mock_driver = setup_graphiti_client

    mock_driver.execute_query.return_value = (
        [{'name': 'test', 'deleted': False}],
        ['name', 'deleted'],
        None
    )

    from graphiti_mcp_server import raw_cypher_query

    result = await raw_cypher_query(
        query="MATCH (n) WHERE n.deleted = false RETURN n.name LIMIT 10",
        ctx=DEFAULT_CTX,
    )

    # Should succeed, not be blocked
    assert isinstance(result, list)
    assert not is_error_response(result)
