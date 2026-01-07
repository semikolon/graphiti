"""Unit tests for group_id extraction from Context.

Tests verify that get_effective_group_id() correctly extracts group_id from:
1. SSE connection custom headers (X-Graphiti-Group-Id)
2. Fallback to config.group_id when header missing
3. Fallback to 'default' when both missing
4. Graceful handling of edge cases
"""

import pytest
from unittest.mock import MagicMock
from mcp.server.fastmcp import Context
from graphiti_mcp_server import get_effective_group_id, config


def test_extracts_from_custom_headers():
    """Verify group_id extracted from X-Graphiti-Group-Id custom header."""
    # Mock Context with custom headers
    ctx = MagicMock(spec=Context)
    mock_request = MagicMock()
    mock_request.headers.get = MagicMock(side_effect=lambda k: "dotfiles" if k == 'X-Graphiti-Group-Id' else None)
    ctx.request_context.request = mock_request

    result = get_effective_group_id(ctx)
    assert result == "dotfiles"


def test_extracts_from_headers_case_insensitive():
    """Verify header extraction is case-insensitive."""
    # Mock Context with lowercase header
    ctx = MagicMock(spec=Context)
    mock_request = MagicMock()
    mock_request.headers.get = MagicMock(side_effect=lambda k: "brf-auto" if k == 'x-graphiti-group-id' else None)
    ctx.request_context.request = mock_request

    result = get_effective_group_id(ctx)
    assert result == "brf-auto"


def test_falls_back_to_config():
    """Verify fallback to config.group_id when no custom header."""
    # Mock Context without custom headers
    ctx = MagicMock(spec=Context)
    mock_request = MagicMock()
    mock_request.headers.get = MagicMock(return_value=None)
    mock_request.query_params.get = MagicMock(return_value=None)
    ctx.request_context.request = mock_request

    # Set config.group_id
    original_group_id = config.group_id
    config.group_id = "cli-group"

    try:
        result = get_effective_group_id(ctx)
        assert result == "cli-group"
    finally:
        config.group_id = original_group_id


def test_falls_back_to_default():
    """Verify fallback to 'default' when Context is None."""
    original_group_id = config.group_id
    config.group_id = None

    try:
        result = get_effective_group_id(None)
        assert result == "default"
    finally:
        config.group_id = original_group_id


def test_ignores_empty_header():
    """Verify empty/whitespace header value is ignored."""
    ctx = MagicMock(spec=Context)
    mock_request = MagicMock()
    mock_request.headers.get = MagicMock(side_effect=lambda k: "  " if k in ['X-Graphiti-Group-Id', 'x-graphiti-group-id'] else None)
    mock_request.query_params.get = MagicMock(return_value=None)
    ctx.request_context.request = mock_request

    original_group_id = config.group_id
    config.group_id = "cli-group"

    try:
        result = get_effective_group_id(ctx)
        assert result == "cli-group"  # Falls back to config
    finally:
        config.group_id = original_group_id


def test_context_meta_not_available():
    """Verify graceful handling when request is None."""
    ctx = MagicMock(spec=Context)
    ctx.request_context.request = None

    original_group_id = config.group_id
    config.group_id = "cli-group"

    try:
        result = get_effective_group_id(ctx)
        assert result == "cli-group"  # Falls back gracefully
    finally:
        config.group_id = original_group_id


def test_headers_dict_missing():
    """Verify graceful handling when request has no headers attribute."""
    ctx = MagicMock(spec=Context)
    mock_request = MagicMock()
    del mock_request.headers  # Remove headers attribute
    mock_request.query_params.get = MagicMock(return_value=None)
    ctx.request_context.request = mock_request

    original_group_id = config.group_id
    config.group_id = "fallback-group"

    try:
        result = get_effective_group_id(ctx)
        assert result == "fallback-group"
    finally:
        config.group_id = original_group_id


def test_precedence_order():
    """Verify precedence: custom header > config.group_id > 'default'."""
    original_group_id = config.group_id

    try:
        # Test 1: Header takes precedence over config
        ctx = MagicMock(spec=Context)
        mock_request = MagicMock()
        mock_request.headers.get = MagicMock(side_effect=lambda k: "from-header" if k == 'X-Graphiti-Group-Id' else None)
        ctx.request_context.request = mock_request
        config.group_id = "from-config"

        result = get_effective_group_id(ctx)
        assert result == "from-header"

        # Test 2: Config takes precedence over 'default'
        mock_request.headers.get = MagicMock(return_value=None)
        mock_request.query_params.get = MagicMock(return_value=None)
        result = get_effective_group_id(ctx)
        assert result == "from-config"

        # Test 3: 'default' when both missing
        config.group_id = None
        result = get_effective_group_id(ctx)
        assert result == "default"
    finally:
        config.group_id = original_group_id


def test_special_characters_in_group_id():
    """Verify special characters in group_id are preserved."""
    ctx = MagicMock(spec=Context)
    mock_request = MagicMock()
    mock_request.headers.get = MagicMock(side_effect=lambda k: "my-project_123.test" if k == 'X-Graphiti-Group-Id' else None)
    ctx.request_context.request = mock_request

    result = get_effective_group_id(ctx)
    assert result == "my-project_123.test"
