"""Regression tests for the notifications module.

Tests verify that:
1. Error accumulator correctly stores and retrieves errors
2. Rate limiting prevents notification spam
3. Notification functions record to accumulator
4. Lua string escaping works correctly
5. Graceful degradation on non-macOS platforms
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
import subprocess

# Import the module under test
from notifications import (
    ProcessingError,
    record_error,
    get_recent_errors_list,
    recent_errors,
    _escape_lua_string,
    _should_rate_limit,
    _last_notification_time,
    send_notification,
    notify_episode_failure,
    notify_search_failure,
    notify_connection_failure,
    is_macos,
    RATE_LIMIT_SECONDS,
)


@pytest.fixture(autouse=True)
def clear_error_accumulator():
    """Clear the error accumulator before each test."""
    recent_errors.clear()
    _last_notification_time.clear()
    yield
    recent_errors.clear()
    _last_notification_time.clear()


class TestProcessingError:
    """Tests for ProcessingError dataclass."""

    def test_processing_error_to_dict(self):
        """ProcessingError.to_dict() returns correct structure."""
        timestamp = datetime(2026, 1, 7, 12, 0, 0)
        error = ProcessingError(
            timestamp=timestamp,
            episode_name="Test Episode",
            group_id="dotfiles",
            error_message="Test error message",
            error_type="episode_processing",
        )

        result = error.to_dict()

        assert result["timestamp"] == "2026-01-07T12:00:00"
        assert result["episode_name"] == "Test Episode"
        assert result["group_id"] == "dotfiles"
        assert result["error_message"] == "Test error message"
        assert result["error_type"] == "episode_processing"


class TestErrorAccumulator:
    """Tests for error accumulator functions."""

    def test_record_error_adds_to_deque(self):
        """record_error() should add error to recent_errors."""
        error = record_error(
            error_type="episode_processing",
            error_message="RediSearch syntax error",
            episode_name="Test Episode",
            group_id="dotfiles",
        )

        assert len(recent_errors) == 1
        assert recent_errors[0].error_type == "episode_processing"
        assert recent_errors[0].error_message == "RediSearch syntax error"

    def test_record_error_returns_error_object(self):
        """record_error() should return the created ProcessingError."""
        error = record_error(
            error_type="search",
            error_message="Connection failed",
            episode_name="",
            group_id="brf-auto",
        )

        assert isinstance(error, ProcessingError)
        assert error.error_type == "search"
        assert error.group_id == "brf-auto"

    def test_ring_buffer_maxlen(self):
        """Error accumulator should have maxlen=100 (ring buffer)."""
        # Add 105 errors
        for i in range(105):
            record_error(
                error_type="test",
                error_message=f"Error {i}",
            )

        # Should only keep last 100
        assert len(recent_errors) == 100
        # First error should be Error 5 (0-4 evicted)
        assert recent_errors[0].error_message == "Error 5"
        # Last error should be Error 104
        assert recent_errors[-1].error_message == "Error 104"

    def test_get_recent_errors_list_time_filter(self):
        """get_recent_errors_list() should filter by time."""
        # Add an old error (manually set timestamp)
        old_error = ProcessingError(
            timestamp=datetime.now() - timedelta(hours=2),
            episode_name="Old Episode",
            group_id="test",
            error_message="Old error",
            error_type="test",
        )
        recent_errors.append(old_error)

        # Add a recent error
        record_error(
            error_type="test",
            error_message="Recent error",
        )

        # Get errors from last 60 minutes
        results = get_recent_errors_list(since_minutes=60)

        assert len(results) == 1
        assert results[0]["error_message"] == "Recent error"

    def test_get_recent_errors_list_type_filter(self):
        """get_recent_errors_list() should filter by error_type."""
        record_error(error_type="episode_processing", error_message="Ep error")
        record_error(error_type="search", error_message="Search error")
        record_error(error_type="connection", error_message="Conn error")

        results = get_recent_errors_list(error_type="search")

        assert len(results) == 1
        assert results[0]["error_type"] == "search"

    def test_get_recent_errors_list_empty(self):
        """get_recent_errors_list() should return empty list when no errors."""
        results = get_recent_errors_list()
        assert results == []


class TestRateLimiting:
    """Tests for notification rate limiting."""

    def test_should_rate_limit_first_call_returns_false(self):
        """First notification should not be rate limited."""
        result = _should_rate_limit("test:key")
        assert result is False

    def test_should_rate_limit_immediate_second_call_returns_true(self):
        """Immediate second call should be rate limited."""
        _should_rate_limit("test:key")  # First call
        result = _should_rate_limit("test:key")  # Immediate second
        assert result is True

    def test_should_rate_limit_different_keys_independent(self):
        """Different keys should be rate limited independently."""
        result1 = _should_rate_limit("key:one")
        result2 = _should_rate_limit("key:two")

        assert result1 is False
        assert result2 is False

    def test_should_rate_limit_expired(self):
        """After RATE_LIMIT_SECONDS, should not be rate limited."""
        # Set last notification time to past
        _last_notification_time["test:expired"] = datetime.now() - timedelta(
            seconds=RATE_LIMIT_SECONDS + 1
        )

        result = _should_rate_limit("test:expired")
        assert result is False


class TestLuaEscaping:
    """Tests for Lua string escaping."""

    def test_escape_backslash(self):
        """Backslashes should be escaped."""
        assert _escape_lua_string("path\\to\\file") == "path\\\\to\\\\file"

    def test_escape_double_quote(self):
        """Double quotes should be escaped."""
        assert _escape_lua_string('say "hello"') == 'say \\"hello\\"'

    def test_escape_single_quote(self):
        """Single quotes should be escaped."""
        assert _escape_lua_string("it's working") == "it\\'s working"

    def test_escape_newline(self):
        """Newlines should be escaped."""
        assert _escape_lua_string("line1\nline2") == "line1\\nline2"

    def test_escape_complex_string(self):
        """Complex strings with multiple special chars."""
        input_str = 'Error: "can\'t parse\\path"\nDetails here'
        expected = "Error: \\\"can\\'t parse\\\\path\\\"\\nDetails here"
        assert _escape_lua_string(input_str) == expected


class TestNotifyEpisodeFailure:
    """Tests for notify_episode_failure convenience function."""

    @patch("notifications.send_notification")
    def test_notify_episode_failure_records_to_accumulator(self, mock_send):
        """notify_episode_failure should record error to accumulator."""
        mock_send.return_value = True

        notify_episode_failure(
            episode_name="Test Episode",
            group_id="dotfiles",
            error="RediSearch syntax error at offset 14",
        )

        assert len(recent_errors) == 1
        assert recent_errors[0].error_type == "episode_processing"
        assert recent_errors[0].episode_name == "Test Episode"
        assert recent_errors[0].group_id == "dotfiles"

    @patch("notifications.send_notification")
    def test_notify_episode_failure_calls_send_notification(self, mock_send):
        """notify_episode_failure should call send_notification."""
        mock_send.return_value = True

        notify_episode_failure(
            episode_name="Test",
            group_id="test",
            error="Test error",
        )

        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert "⚠️ Graphiti Error" in call_args[1]["title"]


class TestNotifySearchFailure:
    """Tests for notify_search_failure convenience function."""

    @patch("notifications.send_notification")
    def test_notify_search_failure_records_to_accumulator(self, mock_send):
        """notify_search_failure should record error to accumulator."""
        mock_send.return_value = True

        notify_search_failure(
            operation="search_nodes",
            group_id="dotfiles",
            error="Connection timeout",
        )

        assert len(recent_errors) == 1
        assert recent_errors[0].error_type == "search"


class TestSendNotification:
    """Tests for send_notification function."""

    @patch("notifications.NOTIFICATIONS_ENABLED", False)
    def test_send_notification_disabled_returns_false(self):
        """When notifications disabled, should return False."""
        result = send_notification("Title", "Body")
        assert result is False

    @patch("notifications.is_macos")
    @patch("notifications.NOTIFICATIONS_ENABLED", True)
    def test_send_notification_non_macos_returns_false(self, mock_is_macos):
        """On non-macOS platforms, should return False."""
        mock_is_macos.return_value = False

        result = send_notification("Title", "Body")
        assert result is False

    @patch("notifications.HS_CLI_PATH", None)
    @patch("notifications.is_macos")
    @patch("notifications.NOTIFICATIONS_ENABLED", True)
    def test_send_notification_no_hammerspoon_returns_false(self, mock_is_macos):
        """When Hammerspoon CLI not found, should return False."""
        mock_is_macos.return_value = True

        result = send_notification("Title", "Body")
        assert result is False

    @patch("notifications.subprocess.run")
    @patch("notifications.HS_CLI_PATH", "/usr/local/bin/hs")
    @patch("notifications.is_macos")
    @patch("notifications.NOTIFICATIONS_ENABLED", True)
    def test_send_notification_success(self, mock_is_macos, mock_run):
        """Successful notification should return True."""
        mock_is_macos.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        result = send_notification("Title", "Body")

        assert result is True
        mock_run.assert_called_once()

    @patch("notifications.subprocess.run")
    @patch("notifications.HS_CLI_PATH", "/usr/local/bin/hs")
    @patch("notifications.is_macos")
    @patch("notifications.NOTIFICATIONS_ENABLED", True)
    def test_send_notification_hammerspoon_failure(self, mock_is_macos, mock_run):
        """When Hammerspoon returns non-zero, should return False."""
        mock_is_macos.return_value = True
        mock_run.return_value = MagicMock(returncode=1, stderr="Error")

        result = send_notification("Title", "Body")

        assert result is False

    @patch("notifications.subprocess.run")
    @patch("notifications.HS_CLI_PATH", "/usr/local/bin/hs")
    @patch("notifications.is_macos")
    @patch("notifications.NOTIFICATIONS_ENABLED", True)
    def test_send_notification_timeout(self, mock_is_macos, mock_run):
        """When subprocess times out, should return False."""
        mock_is_macos.return_value = True
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="hs", timeout=5)

        result = send_notification("Title", "Body")

        assert result is False


class TestIsMacOS:
    """Tests for is_macos() platform detection."""

    @patch("notifications.platform.system")
    def test_is_macos_darwin(self, mock_system):
        """On Darwin (macOS), should return True."""
        mock_system.return_value = "Darwin"
        assert is_macos() is True

    @patch("notifications.platform.system")
    def test_is_macos_linux(self, mock_system):
        """On Linux, should return False."""
        mock_system.return_value = "Linux"
        assert is_macos() is False

    @patch("notifications.platform.system")
    def test_is_macos_windows(self, mock_system):
        """On Windows, should return False."""
        mock_system.return_value = "Windows"
        assert is_macos() is False
