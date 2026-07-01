from heyclaude.tts import strip_for_speech


def test_strips_code_fences():
    out = strip_for_speech("Here:\n```python\nx=1\n```\nDone.")
    assert "x=1" not in out
    assert "code omitted" in out
    assert "Done." in out


def test_strips_markdown_symbols():
    assert "*" not in strip_for_speech("**bold** and `code`")
