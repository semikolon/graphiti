# macOS System Notification Implementation Plan for Graphiti MCP Server

**Date**: December 17, 2025
**Author**: Claude (with Fredrik)
**Status**: Proposed
**Scope**: graphiti-official fork, Fredrik's development environment

---

## Problem Statement

When `add_memory` is called via MCP, it returns "queued for processing" immediately and processes episodes asynchronously. If processing fails (e.g., RediSearch syntax errors, database connection issues, LLM extraction failures), the error is only logged to `/private/tmp/graphiti.stderr.log` - **the user is never notified**.

### Evidence of Silent Failures

From Dec 14, 2025 logs:
```
2025-12-14 20:28:31,811 - Processing queued episode 'Test Fixtures for Unstract Highlights API Simulation'
2025-12-14 20:28:39,224 - ERROR - RediSearch: Syntax error at offset 14 near brf
2025-12-14 20:28:39,224 - ERROR - Error processing episode '...' for group_id brf-auto: RediSearch: Syntax error...
```

User saw: `{"message": "Episode '...' queued for processing (position: 1)"}`
User learned about failure: **Never** (until manual log inspection days later)

---

## Proposed Solution

Trigger macOS system notifications after episode processing completes or fails, using the native `osascript` command (zero dependencies, works on all macOS versions).

### Notification Types

| Event | Title | Subtitle | Body | Sound |
|-------|-------|----------|------|-------|
| Success | 🧠 Graphiti Memory | Episode Saved | "{episode_name}" saved to {group_id} | Glass |
| Failure | ⚠️ Graphiti Error | Episode Failed | "{episode_name}": {error_summary} | Basso |
| Search Error | ⚠️ Graphiti Error | Search Failed | {operation}: {error_summary} | Sosumi |

---

## Implementation Details

### 1. Notification Module

**File**: `mcp_server/notifications.py`

```python
"""
macOS system notifications for Graphiti MCP Server.

Only active on macOS. Gracefully degrades to no-op on other platforms.
"""

import subprocess
import platform
import logging
from typing import Optional
from enum import Enum

logger = logging.getLogger(__name__)


class NotificationType(Enum):
    SUCCESS = "success"
    ERROR = "error"
    WARNING = "warning"


# Sound mapping for notification types
NOTIFICATION_SOUNDS = {
    NotificationType.SUCCESS: "Glass",
    NotificationType.ERROR: "Basso",
    NotificationType.WARNING: "Sosumi",
}

# Title prefixes
NOTIFICATION_TITLES = {
    NotificationType.SUCCESS: "🧠 Graphiti Memory",
    NotificationType.ERROR: "⚠️ Graphiti Error",
    NotificationType.WARNING: "⚠️ Graphiti Warning",
}


def is_macos() -> bool:
    """Check if running on macOS."""
    return platform.system() == "Darwin"


def escape_applescript_string(s: str) -> str:
    """Escape special characters for AppleScript strings."""
    # Escape backslashes first, then quotes
    return s.replace("\\", "\\\\").replace('"', '\\"')


def send_notification(
    notification_type: NotificationType,
    subtitle: str,
    body: str,
    sound: Optional[str] = None,
) -> bool:
    """
    Send a macOS system notification.

    Args:
        notification_type: Type of notification (affects title, default sound)
        subtitle: Short description (e.g., "Episode Saved", "Search Failed")
        body: Detailed message (e.g., episode name, error message)
        sound: Override default sound (None uses type-appropriate default)

    Returns:
        True if notification was sent successfully, False otherwise.
    """
    if not is_macos():
        logger.debug("Notifications disabled: not running on macOS")
        return False

    title = NOTIFICATION_TITLES.get(notification_type, "Graphiti")
    sound = sound or NOTIFICATION_SOUNDS.get(notification_type, "default")

    # Escape special characters
    title_escaped = escape_applescript_string(title)
    subtitle_escaped = escape_applescript_string(subtitle)
    body_escaped = escape_applescript_string(body)

    # Truncate body to prevent notification overflow (macOS limit ~256 chars visible)
    if len(body_escaped) > 200:
        body_escaped = body_escaped[:197] + "..."

    applescript = (
        f'display notification "{body_escaped}" '
        f'with title "{title_escaped}" '
        f'subtitle "{subtitle_escaped}" '
        f'sound name "{sound}"'
    )

    try:
        result = subprocess.run(
            ["osascript", "-e", applescript],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            logger.warning(f"Notification failed: {result.stderr}")
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.warning("Notification timed out")
        return False
    except Exception as e:
        logger.warning(f"Notification error: {e}")
        return False


# Convenience functions for common notification patterns

def notify_episode_success(episode_name: str, group_id: str) -> bool:
    """Notify that an episode was successfully processed."""
    return send_notification(
        NotificationType.SUCCESS,
        subtitle="Episode Saved",
        body=f'"{episode_name}" saved to {group_id}',
    )


def notify_episode_failure(episode_name: str, group_id: str, error: str) -> bool:
    """Notify that episode processing failed."""
    # Extract first line of error for brevity
    error_summary = error.split("\n")[0][:100]
    return send_notification(
        NotificationType.ERROR,
        subtitle=f"Episode Failed ({group_id})",
        body=f'"{episode_name}": {error_summary}',
    )


def notify_search_failure(operation: str, error: str) -> bool:
    """Notify that a search operation failed."""
    error_summary = error.split("\n")[0][:100]
    return send_notification(
        NotificationType.ERROR,
        subtitle="Search Failed",
        body=f"{operation}: {error_summary}",
    )
```

### 2. Integration Points

#### A. Episode Processing Queue (`graphiti_mcp_server.py`)

**Location**: `process_episode_queue()` function (lines 728-759)

**Current code**:
```python
async def process_episode_queue(group_id: str):
    # ...
    try:
        while True:
            process_func = await episode_queues[group_id].get()
            try:
                await process_func()
            except Exception as e:
                logger.error(f'Error processing queued episode for group_id {group_id}: {str(e)}')
            finally:
                episode_queues[group_id].task_done()
    # ...
```

**Modified code**:
```python
from notifications import notify_episode_success, notify_episode_failure

async def process_episode_queue(group_id: str):
    # ...
    try:
        while True:
            process_func = await episode_queues[group_id].get()

            # Extract episode name from closure (if available)
            episode_name = getattr(process_func, '_episode_name', 'Unknown Episode')

            try:
                await process_func()
                # SUCCESS: Notify user
                notify_episode_success(episode_name, group_id)
                logger.info(f"Successfully processed episode '{episode_name}' for group_id {group_id}")
            except Exception as e:
                error_msg = str(e)
                logger.error(f'Error processing queued episode for group_id {group_id}: {error_msg}')
                # FAILURE: Notify user
                notify_episode_failure(episode_name, group_id, error_msg)
            finally:
                episode_queues[group_id].task_done()
    # ...
```

#### B. Attaching Episode Name to Process Function

**Location**: `add_memory()` function (around line 883)

**Modification**: Store episode name on the closure for later retrieval:

```python
# Before putting on queue:
async def process_episode():
    # ... existing code ...

# Attach metadata for notification
process_episode._episode_name = name
process_episode._group_id = group_id_str

await episode_queues[group_id_str].put(process_episode)
```

#### C. Search Error Notifications (Optional)

**Location**: Search tool handlers (e.g., `search_nodes`, `search_facts`)

**Example for `search_nodes`**:
```python
except Exception as e:
    error_msg = str(e)
    logger.error(f'Error searching nodes: {error_msg}')
    # Only notify for unexpected errors, not empty results
    if "RediSearch" in error_msg or "Connection" in error_msg:
        notify_search_failure("search_nodes", error_msg)
    return ErrorResponse(error=f'Error searching nodes: {error_msg}')
```

---

## Configuration Options

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GRAPHITI_NOTIFICATIONS` | `true` | Enable/disable notifications |
| `GRAPHITI_NOTIFY_SUCCESS` | `true` | Notify on successful episode processing |
| `GRAPHITI_NOTIFY_ERRORS` | `true` | Notify on processing/search errors |
| `GRAPHITI_NOTIFY_SOUND` | `true` | Play sound with notifications |

### Configuration in `notifications.py`

```python
import os

NOTIFICATIONS_ENABLED = os.getenv("GRAPHITI_NOTIFICATIONS", "true").lower() == "true"
NOTIFY_SUCCESS = os.getenv("GRAPHITI_NOTIFY_SUCCESS", "true").lower() == "true"
NOTIFY_ERRORS = os.getenv("GRAPHITI_NOTIFY_ERRORS", "true").lower() == "true"
USE_SOUND = os.getenv("GRAPHITI_NOTIFY_SOUND", "true").lower() == "true"

def send_notification(...):
    if not NOTIFICATIONS_ENABLED:
        return False
    # ... rest of function

    sound_param = f'sound name "{sound}"' if USE_SOUND else ""
    # ...
```

---

## Error Accumulator (Complementary Feature)

For batch review of errors that occurred while notifications were missed (e.g., Focus mode on):

### Data Structure

```python
# In-memory ring buffer of recent errors (last 100)
from collections import deque
from dataclasses import dataclass
from datetime import datetime

@dataclass
class ProcessingError:
    timestamp: datetime
    episode_name: str
    group_id: str
    error_message: str
    error_type: str  # "episode_processing", "search", "connection"

# Global error buffer
recent_errors: deque[ProcessingError] = deque(maxlen=100)
```

### New MCP Tool: `get_recent_errors`

```python
@mcp.tool()
async def get_recent_errors(
    since_minutes: int = 60,
    error_type: str | None = None,
) -> list[dict]:
    """
    Get recent Graphiti processing errors.

    Args:
        since_minutes: Return errors from last N minutes (default: 60)
        error_type: Filter by type: "episode_processing", "search", "connection"

    Returns:
        List of error records with timestamp, episode_name, group_id, error_message
    """
    cutoff = datetime.now() - timedelta(minutes=since_minutes)
    errors = [
        e for e in recent_errors
        if e.timestamp >= cutoff
        and (error_type is None or e.error_type == error_type)
    ]
    return [asdict(e) for e in errors]
```

---

## Testing Plan

### Unit Tests

```python
# tests/test_notifications.py

import pytest
from unittest.mock import patch, MagicMock
from mcp_server.notifications import (
    send_notification,
    notify_episode_success,
    notify_episode_failure,
    escape_applescript_string,
    NotificationType,
)


def test_escape_applescript_string():
    assert escape_applescript_string('hello') == 'hello'
    assert escape_applescript_string('say "hi"') == 'say \\"hi\\"'
    assert escape_applescript_string('path\\to\\file') == 'path\\\\to\\\\file'


@patch('mcp_server.notifications.platform.system')
def test_notification_disabled_on_non_macos(mock_system):
    mock_system.return_value = "Linux"
    result = send_notification(NotificationType.SUCCESS, "Test", "Body")
    assert result is False


@patch('mcp_server.notifications.subprocess.run')
@patch('mcp_server.notifications.platform.system')
def test_notification_success(mock_system, mock_run):
    mock_system.return_value = "Darwin"
    mock_run.return_value = MagicMock(returncode=0)

    result = notify_episode_success("Test Episode", "my-project")

    assert result is True
    mock_run.assert_called_once()
    call_args = mock_run.call_args[0][0]
    assert call_args[0] == "osascript"
    assert "Test Episode" in call_args[2]


@patch('mcp_server.notifications.subprocess.run')
@patch('mcp_server.notifications.platform.system')
def test_notification_failure_handling(mock_system, mock_run):
    mock_system.return_value = "Darwin"
    mock_run.return_value = MagicMock(returncode=1, stderr="osascript error")

    result = send_notification(NotificationType.ERROR, "Test", "Body")

    assert result is False
```

### Integration Test (Manual)

```bash
# Test notification directly
python3 -c "
from mcp_server.notifications import notify_episode_success, notify_episode_failure

# Test success notification
notify_episode_success('Integration Test Episode', 'test-project')

# Wait 2 seconds
import time; time.sleep(2)

# Test error notification
notify_episode_failure('Failed Episode', 'test-project', 'RediSearch: Syntax error at offset 14')
"
```

---

## Rollout Plan

### Phase 1: Core Implementation (30 min)
1. Create `mcp_server/notifications.py` with all notification functions
2. Add unit tests
3. Test manually with `osascript`

### Phase 2: Episode Processing Integration (20 min)
1. Modify `process_episode_queue()` to call notifications
2. Attach episode metadata to process functions
3. Test end-to-end with `add_memory`

### Phase 3: Error Accumulator (Optional, 30 min)
1. Add `ProcessingError` dataclass and ring buffer
2. Implement `get_recent_errors` MCP tool
3. Add tests

### Phase 4: Configuration (15 min)
1. Add environment variable support
2. Document in README
3. Update shims wrapper to pass through env vars if needed

---

## Future Enhancements

1. **Notification Center Grouping**: Group notifications by project using `group` parameter (requires `terminal-notifier` instead of `osascript`)

2. **Click Actions**: Make notifications clickable to open log file or Claude Code session (requires more advanced notification framework)

3. **Integration with TTS Daemon**: Optionally speak error notifications using existing Fish Audio integration

4. **Slack/Discord Webhooks**: For remote development, send notifications to messaging platforms

---

## Dependencies

- **Required**: None (uses built-in `osascript`)
- **Optional**: `terminal-notifier` (Homebrew) for advanced features

---

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Notification spam during batch operations | Rate limiting: max 1 notification per 5 seconds per group_id |
| Focus Mode blocks notifications | Error accumulator provides fallback review mechanism |
| Performance impact | Notifications sent in background thread, non-blocking |
| osascript not available | Graceful degradation with `is_macos()` check |

---

## Success Criteria

1. ✅ Episode processing failures trigger visible macOS notification
2. ✅ Success notifications appear for completed episodes
3. ✅ Notifications work from Graphiti MCP server subprocess
4. ✅ No impact on MCP response latency
5. ✅ Can be disabled via environment variable
6. ✅ Works across all macOS versions (10.15+)
