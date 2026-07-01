from heyclaude.brain import (
    build_command,
    classify_claude_error,
    ClaudeUsageLimitError,
    ClaudeAuthError,
)


def test_classify_usage_limit():
    assert classify_claude_error("Claude usage limit reached. Resets at 5pm.") is ClaudeUsageLimitError
    assert classify_claude_error("Error: rate limit exceeded") is ClaudeUsageLimitError
    assert classify_claude_error("429 too many requests") is ClaudeUsageLimitError


def test_classify_auth():
    assert classify_claude_error("You are not logged in. Run claude login.") is ClaudeAuthError
    assert classify_claude_error("invalid api key") is ClaudeAuthError
    assert classify_claude_error("401 unauthorized") is ClaudeAuthError


def test_classify_unknown_returns_none():
    assert classify_claude_error("some unrelated traceback") is None
    assert classify_claude_error("") is None
    assert classify_claude_error(None) is None


def test_first_turn_has_no_resume(tmp_path):
    sess = tmp_path / ".heyclaude_session"
    cmd = build_command("hello", sess)
    assert "--resume" not in cmd
    assert cmd[:2] == ["claude", "-p"]
    assert "hello" in cmd


def test_later_turn_resumes_saved_session(tmp_path):
    sess = tmp_path / ".heyclaude_session"
    sess.write_text("abc-123")
    cmd = build_command("hello", sess)
    assert "--resume" in cmd and "abc-123" in cmd


def test_empty_session_file_has_no_resume(tmp_path):
    sess = tmp_path / ".heyclaude_session"
    sess.write_text("   ")
    cmd = build_command("hello", sess)
    assert "--resume" not in cmd
