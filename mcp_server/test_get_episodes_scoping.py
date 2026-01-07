"""Regression tests for get_episodes and get_global_episodes scoping.

Tests verify that:
1. get_episodes uses SSE context group_id (auto-scopes to project)
2. get_global_episodes always uses 'default' group (cross-project)
3. Explicit group_id parameter overrides context
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timezone
from mcp.server.fastmcp import Context


def create_context_with_query_param(group_id: str) -> Context:
    """Create mock Context with group_id in query params (SSE connection style)."""
    ctx = MagicMock(spec=Context)
    mock_request = MagicMock()
    mock_request.headers.get = MagicMock(return_value=None)
    mock_request.query_params.get = MagicMock(
        side_effect=lambda k, default=None: group_id if k == 'group_id' else default
    )
    ctx.request_context.request = mock_request
    return ctx


def create_context_with_header(group_id: str) -> Context:
    """Create mock Context with group_id in headers."""
    ctx = MagicMock(spec=Context)
    mock_request = MagicMock()
    mock_request.headers.get = MagicMock(
        side_effect=lambda k: group_id if k in ['X-Graphiti-Group-Id', 'x-graphiti-group-id'] else None
    )
    mock_request.query_params.get = MagicMock(return_value=None)
    ctx.request_context.request = mock_request
    return ctx


def create_context_no_group() -> Context:
    """Create mock Context with no group_id (falls back to config.group_id)."""
    ctx = MagicMock(spec=Context)
    mock_request = MagicMock()
    mock_request.headers.get = MagicMock(return_value=None)
    mock_request.query_params.get = MagicMock(return_value=None)
    ctx.request_context.request = mock_request
    return ctx


@pytest.fixture
def mock_graphiti_client():
    """Mock graphiti_client for testing."""
    with patch('graphiti_mcp_server.graphiti_client') as mock:
        mock.retrieve_episodes = AsyncMock(return_value=[])
        yield mock


@pytest.fixture
def mock_episode():
    """Create a mock episode for testing."""
    episode = MagicMock()
    episode.model_dump = MagicMock(return_value={
        'uuid': 'test-uuid-123',
        'name': 'Test Episode',
        'group_id': 'dotfiles',
        'content': 'Test content',
        'created_at': '2026-01-07T12:00:00Z',
    })
    return episode


class TestGetEpisodesScoping:
    """Tests for get_episodes auto-scoping via SSE context."""

    @pytest.mark.asyncio
    async def test_get_episodes_uses_context_group_id_from_query_param(self, mock_graphiti_client, mock_episode):
        """get_episodes should use GROUP_ID from SSE query params."""
        from graphiti_mcp_server import get_episodes

        mock_graphiti_client.retrieve_episodes.return_value = [mock_episode]
        ctx = create_context_with_query_param("dotfiles")

        result = await get_episodes(ctx=ctx, last_n=5)

        # Verify retrieve_episodes was called with correct group_id
        mock_graphiti_client.retrieve_episodes.assert_called_once()
        call_kwargs = mock_graphiti_client.retrieve_episodes.call_args[1]
        assert call_kwargs['group_ids'] == ['dotfiles']
        assert call_kwargs['last_n'] == 5

    @pytest.mark.asyncio
    async def test_get_episodes_uses_context_group_id_from_header(self, mock_graphiti_client, mock_episode):
        """get_episodes should use group_id from headers as fallback."""
        from graphiti_mcp_server import get_episodes

        mock_graphiti_client.retrieve_episodes.return_value = [mock_episode]
        ctx = create_context_with_header("brf-auto")

        result = await get_episodes(ctx=ctx, last_n=3)

        mock_graphiti_client.retrieve_episodes.assert_called_once()
        call_kwargs = mock_graphiti_client.retrieve_episodes.call_args[1]
        assert call_kwargs['group_ids'] == ['brf-auto']

    @pytest.mark.asyncio
    async def test_get_episodes_explicit_group_id_overrides_context(self, mock_graphiti_client, mock_episode):
        """Explicit group_id parameter should override context."""
        from graphiti_mcp_server import get_episodes

        mock_graphiti_client.retrieve_episodes.return_value = [mock_episode]
        ctx = create_context_with_query_param("dotfiles")

        # Pass explicit group_id that differs from context
        result = await get_episodes(ctx=ctx, group_id="explicit-group", last_n=5)

        mock_graphiti_client.retrieve_episodes.assert_called_once()
        call_kwargs = mock_graphiti_client.retrieve_episodes.call_args[1]
        assert call_kwargs['group_ids'] == ['explicit-group']

    @pytest.mark.asyncio
    async def test_get_episodes_returns_formatted_episodes(self, mock_graphiti_client, mock_episode):
        """get_episodes should return properly formatted episode list."""
        from graphiti_mcp_server import get_episodes

        mock_graphiti_client.retrieve_episodes.return_value = [mock_episode]
        ctx = create_context_with_query_param("dotfiles")

        result = await get_episodes(ctx=ctx, last_n=5)

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]['uuid'] == 'test-uuid-123'
        assert result[0]['name'] == 'Test Episode'

    @pytest.mark.asyncio
    async def test_get_episodes_empty_result(self, mock_graphiti_client):
        """get_episodes should handle empty results gracefully."""
        from graphiti_mcp_server import get_episodes

        mock_graphiti_client.retrieve_episodes.return_value = []
        ctx = create_context_with_query_param("empty-project")

        result = await get_episodes(ctx=ctx, last_n=5)

        # Should return EpisodeSearchResponse with message
        assert 'message' in result
        assert 'No episodes found' in result['message']


class TestGetGlobalEpisodesScoping:
    """Tests for get_global_episodes hardcoded 'default' group."""

    @pytest.mark.asyncio
    async def test_get_global_episodes_uses_default_group(self, mock_graphiti_client, mock_episode):
        """get_global_episodes should always use 'default' group."""
        from graphiti_mcp_server import get_global_episodes

        mock_graphiti_client.retrieve_episodes.return_value = [mock_episode]

        result = await get_global_episodes(last_n=5)

        mock_graphiti_client.retrieve_episodes.assert_called_once()
        call_kwargs = mock_graphiti_client.retrieve_episodes.call_args[1]
        assert call_kwargs['group_ids'] == ['default']

    @pytest.mark.asyncio
    async def test_get_global_episodes_ignores_any_context(self, mock_graphiti_client, mock_episode):
        """get_global_episodes should use 'default' regardless of any ambient context."""
        from graphiti_mcp_server import get_global_episodes

        mock_graphiti_client.retrieve_episodes.return_value = [mock_episode]

        # Even if there were some ambient context, global should ignore it
        result = await get_global_episodes(last_n=10)

        mock_graphiti_client.retrieve_episodes.assert_called_once()
        call_kwargs = mock_graphiti_client.retrieve_episodes.call_args[1]
        assert call_kwargs['group_ids'] == ['default']
        assert call_kwargs['last_n'] == 10

    @pytest.mark.asyncio
    async def test_get_global_episodes_empty_result(self, mock_graphiti_client):
        """get_global_episodes should handle empty results gracefully."""
        from graphiti_mcp_server import get_global_episodes

        mock_graphiti_client.retrieve_episodes.return_value = []

        result = await get_global_episodes(last_n=5)

        assert 'message' in result
        assert 'No episodes found in global memory' in result['message']


class TestGetEpisodesFallback:
    """Tests for fallback behavior when context has no group_id."""

    @pytest.mark.asyncio
    async def test_get_episodes_falls_back_to_default(self, mock_graphiti_client, mock_episode):
        """When context has no group_id, should fall back to 'default'."""
        from graphiti_mcp_server import get_episodes

        mock_graphiti_client.retrieve_episodes.return_value = [mock_episode]
        ctx = create_context_no_group()

        result = await get_episodes(ctx=ctx, last_n=5)

        mock_graphiti_client.retrieve_episodes.assert_called_once()
        call_kwargs = mock_graphiti_client.retrieve_episodes.call_args[1]
        # Falls back through: context (None) -> config.group_id (may be None) -> "default"
        assert call_kwargs['group_ids'] == ['default']
