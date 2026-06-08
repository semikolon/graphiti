"""Tests for the two error-observability fixes (2026-06-08):
1. classify_processing_error disambiguates insufficient_quota from rate limit.
2. get_recent_errors_list reads the persisted JSONL (survives restarts)."""
import json
from datetime import datetime, timedelta

import notifications


def test_classify_detects_insufficient_quota_in_chain():
    class FakeOpenAIError(Exception):
        code = "insufficient_quota"

    try:
        try:
            raise FakeOpenAIError("You exceeded your current quota, check your plan and billing")
        except FakeOpenAIError as oe:
            raise RuntimeError("Rate limit exceeded. Please try again later.") from oe
    except RuntimeError as e:
        msg = notifications.classify_processing_error(e, "RateLimitError: Rate limit exceeded. Please try again later.")
    assert "InsufficientQuota" in msg
    assert "billing" in msg.lower()
    assert "retrying will NOT help" in msg


def test_classify_leaves_real_rate_limit_alone():
    e = RuntimeError("429 Too Many Requests")
    base = "RateLimitError: Rate limit exceeded"
    assert notifications.classify_processing_error(e, base) == base


def test_get_recent_errors_reads_jsonl_after_restart(tmp_path, monkeypatch):
    p = tmp_path / "errors.jsonl"
    rec = {
        "timestamp": datetime.now().isoformat(),
        "episode_name": "DIM test episode",
        "group_id": "din-mamma",
        "error_message": "InsufficientQuotaError ...",
        "error_type": "episode_processing",
    }
    p.write_text(json.dumps(rec) + "\n")
    monkeypatch.setattr(notifications, "ERROR_LOG_PATH", str(p))
    notifications.recent_errors.clear()  # simulate a daemon restart (deque empty)
    out = notifications.get_recent_errors_list(since_minutes=60)
    assert len(out) == 1
    assert out[0]["episode_name"] == "DIM test episode"


def test_get_recent_errors_respects_window_and_dedupes(tmp_path, monkeypatch):
    p = tmp_path / "errors.jsonl"
    old = {"timestamp": (datetime.now() - timedelta(hours=3)).isoformat(), "episode_name": "old", "group_id": "g", "error_message": "x", "error_type": "episode_processing"}
    recent = {"timestamp": datetime.now().isoformat(), "episode_name": "new", "group_id": "g", "error_message": "y", "error_type": "episode_processing"}
    p.write_text(json.dumps(old) + "\n" + json.dumps(recent) + "\n" + json.dumps(recent) + "\n")
    monkeypatch.setattr(notifications, "ERROR_LOG_PATH", str(p))
    notifications.recent_errors.clear()
    out = notifications.get_recent_errors_list(since_minutes=60)
    names = [o["episode_name"] for o in out]
    assert names == ["new"]  # old filtered out, duplicate deduped
