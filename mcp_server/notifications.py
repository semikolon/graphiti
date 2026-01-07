"""
macOS system notifications for Graphiti MCP Server errors.

Uses Hammerspoon for visual overlay notifications (consistent with TTS daemon style).
Only active on macOS. Gracefully degrades to no-op on other platforms.
Error-only notifications to avoid spam - success is expected, errors need attention.
"""

import logging
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from collections import deque
from typing import Optional

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================

NOTIFICATIONS_ENABLED = os.getenv("GRAPHITI_NOTIFICATIONS", "true").lower() == "true"
NOTIFICATION_DURATION = int(os.getenv("GRAPHITI_NOTIFY_DURATION", "10"))  # seconds

# Rate limiting: max 1 notification per N seconds per error type
RATE_LIMIT_SECONDS = 5
_last_notification_time: dict[str, datetime] = {}

# Hammerspoon CLI path (check common locations)
HS_CLI_PATH: Optional[str] = shutil.which("hs") or (
    "/Applications/Hammerspoon.app/Contents/Frameworks/hs/hs"
    if os.path.exists("/Applications/Hammerspoon.app/Contents/Frameworks/hs/hs")
    else None
)


# =============================================================================
# Error Accumulator
# =============================================================================

@dataclass
class ProcessingError:
    """Record of a Graphiti processing error."""
    timestamp: datetime
    episode_name: str
    group_id: str
    error_message: str
    error_type: str  # "episode_processing", "search", "connection"

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "episode_name": self.episode_name,
            "group_id": self.group_id,
            "error_message": self.error_message,
            "error_type": self.error_type,
        }


# Ring buffer of recent errors (last 100)
recent_errors: deque[ProcessingError] = deque(maxlen=100)


def record_error(
    error_type: str,
    error_message: str,
    episode_name: str = "",
    group_id: str = "",
) -> ProcessingError:
    """
    Record an error to the accumulator.

    Args:
        error_type: Category of error ("episode_processing", "search", "connection")
        error_message: The error message/exception text
        episode_name: Name of episode if applicable
        group_id: The group_id context

    Returns:
        The created ProcessingError record
    """
    error = ProcessingError(
        timestamp=datetime.now(),
        episode_name=episode_name,
        group_id=group_id,
        error_message=error_message,
        error_type=error_type,
    )
    recent_errors.append(error)
    return error


def get_recent_errors_list(
    since_minutes: int = 60,
    error_type: Optional[str] = None,
) -> list[dict]:
    """
    Get recent errors from the accumulator.

    Args:
        since_minutes: Return errors from last N minutes
        error_type: Optional filter by type

    Returns:
        List of error dicts
    """
    cutoff = datetime.now() - timedelta(minutes=since_minutes)
    errors = [
        e for e in recent_errors
        if e.timestamp >= cutoff
        and (error_type is None or e.error_type == error_type)
    ]
    return [e.to_dict() for e in errors]


# =============================================================================
# Notification System (Hammerspoon-based)
# =============================================================================

# Amber/yellow style for error alerts (matches warning icon color)
ALERT_STYLE = {
    "strokeWidth": 0,
    "fillColor": {"red": 1.0, "green": 0.8, "blue": 0.2, "alpha": 0.75},
    "textColor": {"red": 0.2, "green": 0.1, "blue": 0.0, "alpha": 1},
    "radius": 20,
    "textSize": 18,
}


def is_macos() -> bool:
    """Check if running on macOS."""
    return platform.system() == "Darwin"


def _should_rate_limit(key: str) -> bool:
    """Check if we should rate-limit this notification."""
    now = datetime.now()
    last_time = _last_notification_time.get(key)

    if last_time and (now - last_time).total_seconds() < RATE_LIMIT_SECONDS:
        return True

    _last_notification_time[key] = now
    return False


def _escape_lua_string(s: str) -> str:
    """Escape special characters for Lua strings."""
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("'", "\\'")
        .replace("\n", "\\n")  # Escape newlines for Lua
    )


def send_notification(
    title: str,
    body: str,
    duration: Optional[int] = None,
) -> bool:
    """
    Send a Hammerspoon visual overlay notification.

    Args:
        title: Main title line
        body: Message body (second line)
        duration: How long to show (seconds), defaults to NOTIFICATION_DURATION

    Returns:
        True if notification was sent successfully, False otherwise.
    """
    if not NOTIFICATIONS_ENABLED:
        logger.debug("Notifications disabled via GRAPHITI_NOTIFICATIONS=false")
        return False

    if not is_macos():
        logger.debug("Notifications disabled: not running on macOS")
        return False

    if not HS_CLI_PATH:
        logger.debug("Notifications disabled: Hammerspoon CLI not found")
        return False

    # Rate limiting
    rate_key = f"graphiti:{title}"
    if _should_rate_limit(rate_key):
        logger.debug(f"Rate-limited notification: {rate_key}")
        return False

    duration = duration or NOTIFICATION_DURATION

    # Escape and truncate
    title_escaped = _escape_lua_string(title)
    body_escaped = _escape_lua_string(body)
    if len(body_escaped) > 120:
        body_escaped = body_escaped[:117] + "..."

    # Single line format: "Title: Body"
    message = f"{title_escaped}: {body_escaped}"

    # Build Lua command for hs.alert
    style_lua = (
        f"{{ strokeWidth = {ALERT_STYLE['strokeWidth']}, "
        f"fillColor = {{ red = {ALERT_STYLE['fillColor']['red']}, green = {ALERT_STYLE['fillColor']['green']}, "
        f"blue = {ALERT_STYLE['fillColor']['blue']}, alpha = {ALERT_STYLE['fillColor']['alpha']} }}, "
        f"textColor = {{ red = {ALERT_STYLE['textColor']['red']}, green = {ALERT_STYLE['textColor']['green']}, "
        f"blue = {ALERT_STYLE['textColor']['blue']}, alpha = {ALERT_STYLE['textColor']['alpha']} }}, "
        f"radius = {ALERT_STYLE['radius']}, textSize = {ALERT_STYLE['textSize']} }}"
    )

    lua_command = f'hs.alert.show("{message}", {style_lua}, {duration})'

    try:
        result = subprocess.run(
            [HS_CLI_PATH, "-c", lua_command],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            logger.warning(f"Hammerspoon notification failed: {result.stderr}")
            return False
        logger.debug(f"Notification sent: {title}")
        return True
    except subprocess.TimeoutExpired:
        logger.warning("Notification timed out")
        return False
    except Exception as e:
        logger.warning(f"Notification error: {e}")
        return False


# =============================================================================
# Convenience Functions
# =============================================================================

def notify_episode_failure(episode_name: str, group_id: str, error: str) -> bool:
    """
    Notify that episode processing failed.
    Also records to error accumulator.
    """
    # Record to accumulator
    record_error(
        error_type="episode_processing",
        error_message=error,
        episode_name=episode_name,
        group_id=group_id,
    )

    # Extract first line of error for brevity
    error_summary = error.split("\n")[0][:100]

    return send_notification(
        title="⚠️ Graphiti Error",
        body=f"Episode Failed ({group_id})\n\"{episode_name}\": {error_summary}",
    )


def notify_search_failure(operation: str, group_id: str, error: str) -> bool:
    """
    Notify that a search operation failed.
    Only for unexpected errors (not empty results).
    Also records to error accumulator.
    """
    # Record to accumulator
    record_error(
        error_type="search",
        error_message=error,
        episode_name="",
        group_id=group_id,
    )

    error_summary = error.split("\n")[0][:100]

    return send_notification(
        title="⚠️ Graphiti Warning",
        body=f"Search Failed ({group_id})\n{operation}: {error_summary}",
    )


def notify_connection_failure(error: str) -> bool:
    """
    Notify that a database connection failed.
    Also records to error accumulator.
    """
    # Record to accumulator
    record_error(
        error_type="connection",
        error_message=error,
        episode_name="",
        group_id="",
    )

    error_summary = error.split("\n")[0][:100]

    return send_notification(
        title="⚠️ Graphiti Error",
        body=f"Connection Failed\n{error_summary}",
    )
