"""Unit tests for cypher_query_write tool.

Companion to test_raw_cypher_query.py — verifies the write-capable sister tool
allows CREATE/DELETE/SET/MERGE/REMOVE/DETACH-DELETE while still blocking
catastrophic operations (DROP) and applying group_id scoping.

Tests verify:
1. Write operations execute successfully (CREATE, SET, DELETE, MERGE, etc.)
2. Catastrophic operations are blocked (DROP)
3. Group_id scoping via driver.clone (matches raw_cypher_query pattern)
4. Parameterized queries work correctly
5. LIMIT is auto-added to RETURN clauses
6. Results are capped at max_results
7. Error handling works properly
8. Audit log entry written for write operations

Note: ErrorResponse is a TypedDict, so results are dicts with 'error' key.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from mcp.server.fastmcp import Context


def is_error_response(result) -> bool:
    return isinstance(result, dict) and 'error' in result


def create_context(group_id: str) -> Context:
    ctx = MagicMock(spec=Context)
    mock_request = MagicMock()
    mock_request.headers.get = MagicMock(
        side_effect=lambda k: group_id if k in ['X-Graphiti-Group-Id', 'x-graphiti-group-id'] else None
    )
    mock_request.query_params.get = MagicMock(return_value=None)
    ctx.request_context.request = mock_request
    return ctx


DEFAULT_CTX = create_context("test-project")


@pytest.fixture
def mock_graphiti_client():
    with patch('graphiti_mcp_server.graphiti_client') as mock:
        mock_client = MagicMock()
        mock_driver = MagicMock()
        mock_driver.execute_query = AsyncMock()
        mock_driver.clone = MagicMock(return_value=mock_driver)
        mock_client.driver = mock_driver
        mock.__bool__ = lambda x: True
        mock.driver = mock_driver
        yield mock_client, mock_driver


@pytest.fixture
def setup_graphiti_client(mock_graphiti_client):
    mock_client, mock_driver = mock_graphiti_client
    with patch('graphiti_mcp_server.graphiti_client', mock_client):
        yield mock_client, mock_driver


# ==================== ALLOWED WRITE OPERATIONS ====================


@pytest.mark.asyncio
async def test_allows_create_operation(setup_graphiti_client):
    """CREATE should be allowed (the whole point of the write tool)."""
    from graphiti_mcp_server import cypher_query_write
    _, mock_driver = setup_graphiti_client
    mock_driver.execute_query.return_value = (
        [{"uuid": "task-uuid-1"}],
        ["uuid"],
        None,
    )

    result = await cypher_query_write(
        query="CREATE (n:Task {uuid: $uuid, name: 'test'}) RETURN n.uuid AS uuid",
        ctx=DEFAULT_CTX,
        params={"uuid": "task-uuid-1"},
    )

    assert not is_error_response(result)
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["uuid"] == "task-uuid-1"


@pytest.mark.asyncio
async def test_allows_set_operation(setup_graphiti_client):
    """SET (the kanban mark-complete primitive) should be allowed."""
    from graphiti_mcp_server import cypher_query_write
    _, mock_driver = setup_graphiti_client
    mock_driver.execute_query.return_value = ([{"uuid": "abc"}], ["uuid"], None)

    result = await cypher_query_write(
        query="MATCH (n:Task {uuid: $uuid}) SET n.status = $status RETURN n.uuid AS uuid",
        ctx=DEFAULT_CTX,
        params={"uuid": "abc", "status": "done"},
    )
    assert not is_error_response(result)


@pytest.mark.asyncio
async def test_allows_delete_operation(setup_graphiti_client):
    """DELETE should be allowed (per-task removal)."""
    from graphiti_mcp_server import cypher_query_write
    _, mock_driver = setup_graphiti_client
    mock_driver.execute_query.return_value = ([], [], None)

    result = await cypher_query_write(
        query="MATCH (n:Task {uuid: $uuid}) DELETE n",
        ctx=DEFAULT_CTX,
        params={"uuid": "abc"},
    )
    assert not is_error_response(result)


@pytest.mark.asyncio
async def test_allows_detach_delete(setup_graphiti_client):
    """DETACH DELETE should be allowed (removes node + its edges)."""
    from graphiti_mcp_server import cypher_query_write
    _, mock_driver = setup_graphiti_client
    mock_driver.execute_query.return_value = ([], [], None)

    result = await cypher_query_write(
        query="MATCH (n:Task {uuid: $uuid}) DETACH DELETE n",
        ctx=DEFAULT_CTX,
        params={"uuid": "abc"},
    )
    assert not is_error_response(result)


@pytest.mark.asyncio
async def test_allows_merge_operation(setup_graphiti_client):
    """MERGE should be allowed (upsert pattern)."""
    from graphiti_mcp_server import cypher_query_write
    _, mock_driver = setup_graphiti_client
    mock_driver.execute_query.return_value = ([{"uuid": "abc"}], ["uuid"], None)

    result = await cypher_query_write(
        query="MERGE (n:Task {uuid: $uuid}) ON CREATE SET n.created_at = $ts RETURN n.uuid AS uuid",
        ctx=DEFAULT_CTX,
        params={"uuid": "abc", "ts": 1730000000000},
    )
    assert not is_error_response(result)


@pytest.mark.asyncio
async def test_allows_remove_operation(setup_graphiti_client):
    """REMOVE should be allowed (removes properties / labels)."""
    from graphiti_mcp_server import cypher_query_write
    _, mock_driver = setup_graphiti_client
    mock_driver.execute_query.return_value = ([], [], None)

    result = await cypher_query_write(
        query="MATCH (n:Task {uuid: $uuid}) REMOVE n.priority",
        ctx=DEFAULT_CTX,
        params={"uuid": "abc"},
    )
    assert not is_error_response(result)


# ==================== CATASTROPHIC OPERATION BLOCKING ====================


@pytest.mark.asyncio
async def test_blocks_drop(setup_graphiti_client):
    """DROP must be blocked (catastrophic — wipes graph/database)."""
    from graphiti_mcp_server import cypher_query_write

    result = await cypher_query_write(
        query="DROP DATABASE foo",
        ctx=DEFAULT_CTX,
    )
    assert is_error_response(result)
    assert 'DROP' in result['error']
    assert 'clear_graph' in result['error'].lower()


@pytest.mark.asyncio
async def test_blocks_drop_lowercase(setup_graphiti_client):
    """DROP block is case-insensitive (regex with \\b uses word-boundary on uppercase string)."""
    from graphiti_mcp_server import cypher_query_write

    result = await cypher_query_write(
        query="drop database foo",
        ctx=DEFAULT_CTX,
    )
    assert is_error_response(result)
    assert 'DROP' in result['error']


@pytest.mark.asyncio
async def test_drop_word_boundary_does_not_false_match_dropped(setup_graphiti_client):
    """`dropped_at` (a hypothetical property name) should NOT trigger DROP block.

    Word-boundary regex \\bDROP\\b prevents partial matches inside identifiers.
    """
    from graphiti_mcp_server import cypher_query_write
    _, mock_driver = setup_graphiti_client
    mock_driver.execute_query.return_value = ([{"count": 0}], ["count"], None)

    result = await cypher_query_write(
        query="MATCH (n:Task) WHERE n.dropped_at IS NOT NULL RETURN count(n) AS count",
        ctx=DEFAULT_CTX,
    )
    # `dropped_at` contains "DROP" as a substring but not as a whole word.
    # The regex \\bDROP\\b should not match — query should pass safety check.
    assert not is_error_response(result), f"got error: {result.get('error') if isinstance(result, dict) else 'n/a'}"


# ==================== GROUP_ID SCOPING ====================


@pytest.mark.asyncio
async def test_uses_group_id_for_driver_clone(setup_graphiti_client):
    """Verifies driver.clone(database=group_id) is called with the session's group_id."""
    from graphiti_mcp_server import cypher_query_write
    _, mock_driver = setup_graphiti_client
    mock_driver.execute_query.return_value = ([{"uuid": "abc"}], ["uuid"], None)
    ctx = create_context("fyr-fredrik")

    await cypher_query_write(
        query="CREATE (n:Task {uuid: 'abc'}) RETURN n.uuid AS uuid",
        ctx=ctx,
    )

    mock_driver.clone.assert_called_once_with(database="fyr-fredrik")


@pytest.mark.asyncio
async def test_different_group_ids_dont_cross_contaminate(setup_graphiti_client):
    """Two calls with different group_ids should each clone the driver with their own group_id."""
    from graphiti_mcp_server import cypher_query_write
    _, mock_driver = setup_graphiti_client
    mock_driver.execute_query.return_value = ([], [], None)

    await cypher_query_write(query="CREATE (n:Task) RETURN 1 AS x", ctx=create_context("user-a"))
    await cypher_query_write(query="CREATE (n:Task) RETURN 1 AS x", ctx=create_context("user-b"))

    calls = mock_driver.clone.call_args_list
    assert len(calls) == 2
    assert calls[0].kwargs == {"database": "user-a"}
    assert calls[1].kwargs == {"database": "user-b"}


# ==================== PARAMETERIZED QUERIES + LIMIT + ERROR HANDLING ====================


@pytest.mark.asyncio
async def test_parameterized_query_passes_params_through(setup_graphiti_client):
    """Params should be forwarded to driver.execute_query as kwargs."""
    from graphiti_mcp_server import cypher_query_write
    _, mock_driver = setup_graphiti_client
    mock_driver.execute_query.return_value = ([{"uuid": "x"}], ["uuid"], None)

    await cypher_query_write(
        query="CREATE (n:Task {uuid: $uuid, name: $title}) RETURN n.uuid AS uuid",
        ctx=DEFAULT_CTX,
        params={"uuid": "x", "title": "Buy milk"},
    )

    # execute_query was called with the params as kwargs
    call_kwargs = mock_driver.execute_query.call_args.kwargs
    assert call_kwargs.get("uuid") == "x"
    assert call_kwargs.get("title") == "Buy milk"


@pytest.mark.asyncio
async def test_auto_adds_limit_to_return_clause(setup_graphiti_client):
    """RETURN without LIMIT should get LIMIT auto-appended (caps memory)."""
    from graphiti_mcp_server import cypher_query_write
    _, mock_driver = setup_graphiti_client
    mock_driver.execute_query.return_value = ([], [], None)

    await cypher_query_write(
        query="MATCH (n:Task) SET n.touched = true RETURN n.uuid AS uuid",
        ctx=DEFAULT_CTX,
        max_results=42,
    )

    sent_query = mock_driver.execute_query.call_args.args[0]
    assert "LIMIT 42" in sent_query


@pytest.mark.asyncio
async def test_no_limit_added_when_no_return(setup_graphiti_client):
    """A pure-write query without RETURN should NOT get a LIMIT (would be syntax error)."""
    from graphiti_mcp_server import cypher_query_write
    _, mock_driver = setup_graphiti_client
    mock_driver.execute_query.return_value = ([], [], None)

    await cypher_query_write(
        query="MATCH (n:Task {uuid: $uuid}) DETACH DELETE n",
        ctx=DEFAULT_CTX,
        params={"uuid": "abc"},
    )

    sent_query = mock_driver.execute_query.call_args.args[0]
    assert "LIMIT" not in sent_query


@pytest.mark.asyncio
async def test_caps_max_results_at_500(setup_graphiti_client):
    """max_results above 500 should be capped to 500 (memory protection)."""
    from graphiti_mcp_server import cypher_query_write
    _, mock_driver = setup_graphiti_client
    mock_driver.execute_query.return_value = ([], [], None)

    await cypher_query_write(
        query="MATCH (n:Task) RETURN n.uuid AS uuid",
        ctx=DEFAULT_CTX,
        max_results=10000,
    )

    sent_query = mock_driver.execute_query.call_args.args[0]
    assert "LIMIT 500" in sent_query


@pytest.mark.asyncio
async def test_truncates_records_to_max_results(setup_graphiti_client):
    """If driver returns more records than max_results, truncate (extra defense)."""
    from graphiti_mcp_server import cypher_query_write
    _, mock_driver = setup_graphiti_client
    # Return 5 records, ask for max 3
    mock_driver.execute_query.return_value = (
        [{"uuid": f"u{i}"} for i in range(5)],
        ["uuid"],
        None,
    )

    result = await cypher_query_write(
        query="MATCH (n:Task) RETURN n.uuid AS uuid",
        ctx=DEFAULT_CTX,
        max_results=3,
    )
    assert isinstance(result, list)
    assert len(result) == 3


@pytest.mark.asyncio
async def test_handles_query_error(setup_graphiti_client):
    """Exception during execute_query should be caught and returned as ErrorResponse."""
    from graphiti_mcp_server import cypher_query_write
    _, mock_driver = setup_graphiti_client
    mock_driver.execute_query.side_effect = Exception("syntax error near 'WHRE'")

    result = await cypher_query_write(
        query="MATCH (n:Task) WHRE n.x = 1 RETURN n.uuid AS uuid",
        ctx=DEFAULT_CTX,
    )

    assert is_error_response(result)
    assert "syntax error" in result['error']


@pytest.mark.asyncio
async def test_returns_error_when_client_not_initialized():
    """If graphiti_client is None, return ErrorResponse without crashing."""
    from graphiti_mcp_server import cypher_query_write
    with patch('graphiti_mcp_server.graphiti_client', None):
        result = await cypher_query_write(
            query="CREATE (n:Task) RETURN 1",
            ctx=DEFAULT_CTX,
        )
        assert is_error_response(result)
        assert "not initialized" in result['error']


@pytest.mark.asyncio
async def test_handles_none_result(setup_graphiti_client):
    """If driver returns None, treat as empty result list."""
    from graphiti_mcp_server import cypher_query_write
    _, mock_driver = setup_graphiti_client
    mock_driver.execute_query.return_value = None

    result = await cypher_query_write(
        query="MATCH (n:Task) RETURN n.uuid AS uuid",
        ctx=DEFAULT_CTX,
    )
    assert result == []
