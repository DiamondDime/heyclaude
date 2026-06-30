from codriver.brain import build_command


def test_first_turn_has_no_resume(tmp_path):
    sess = tmp_path / ".codriver_session"
    cmd = build_command("hello", sess)
    assert "--resume" not in cmd
    assert cmd[:2] == ["claude", "-p"]
    assert "hello" in cmd


def test_later_turn_resumes_saved_session(tmp_path):
    sess = tmp_path / ".codriver_session"
    sess.write_text("abc-123")
    cmd = build_command("hello", sess)
    assert "--resume" in cmd and "abc-123" in cmd


def test_empty_session_file_has_no_resume(tmp_path):
    sess = tmp_path / ".codriver_session"
    sess.write_text("   ")
    cmd = build_command("hello", sess)
    assert "--resume" not in cmd
