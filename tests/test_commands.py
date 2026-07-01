from heyclaude.commands import parse_command, friendly_model


def test_effort_slash():
    assert parse_command("/effort high") == {"action": "set_effort", "value": "high"}


def test_effort_voice():
    assert parse_command("set effort to xhigh")["value"] == "xhigh"
    assert parse_command("effort max")["value"] == "max"
    assert parse_command("set the effort to low")["value"] == "low"
    assert parse_command("high effort")["value"] == "high"


def test_model_slash():
    assert parse_command("/model sonnet")["value"] == "claude-sonnet-4-6"


def test_model_voice_aliases():
    assert parse_command("use opus")["value"] == "claude-opus-4-8"
    assert parse_command("switch to sonnet")["value"] == "claude-sonnet-4-6"
    assert parse_command("use the haiku model")["value"] == "claude-haiku-4-5"


def test_model_explicit_id():
    assert parse_command("/model claude-opus-4-8")["value"] == "claude-opus-4-8"


def test_show_config():
    assert parse_command("/config")["action"] == "show_config"
    assert parse_command("what's my config")["action"] == "show_config"
    assert parse_command("what is my config")["action"] == "show_config"
    assert parse_command("what is my model")["action"] == "show_config"


def test_reset():
    assert parse_command("reset")["action"] == "reset"
    assert parse_command("new session")["action"] == "reset"
    assert parse_command("start over")["action"] == "reset"


def test_normal_prompts_are_not_commands():
    # These must reach Claude, NOT be swallowed as settings changes.
    assert parse_command("add a login button to the homepage") is None
    assert parse_command("use the helper function in utils.py") is None
    assert parse_command("can you use opus for the heavy refactor please") is None
    assert parse_command("reset the database session pool and retry") is None
    assert parse_command("show me the settings file in the config folder") is None
    assert parse_command("refactor the high effort code path") is None
    # the exact false positives the adversarial fuzzer found:
    assert parse_command("set effort to high in the jira story points") is None
    assert parse_command("reduce the effort to medium so we can ship faster") is None
    assert parse_command("switch to claude-foo") is None


def test_friendly_model():
    assert friendly_model("claude-opus-4-8") == "Opus 4.8"
    assert friendly_model("unknown-model") == "unknown-model"
