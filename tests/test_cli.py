from pathlib import Path

from codriver.cli import validate_workdir


def test_refuses_home(tmp_path):
    home = tmp_path / "home" / "alice"
    home.mkdir(parents=True)
    assert validate_workdir(home, home) is not None


def test_refuses_filesystem_root(tmp_path):
    home = tmp_path / "home" / "alice"
    home.mkdir(parents=True)
    assert validate_workdir(Path(home.anchor), home) is not None


def test_refuses_ancestor_of_home(tmp_path):
    # e.g. choosing /Users when home is /Users/alice
    home = tmp_path / "home" / "alice"
    home.mkdir(parents=True)
    assert validate_workdir(home.parent, home) is not None


def test_refuses_sensitive_subdirs(tmp_path):
    home = tmp_path / "home" / "alice"
    home.mkdir(parents=True)
    for name in ("Documents", "Desktop", "Downloads", "Projects", "code"):
        assert validate_workdir(home / name, home) is not None, name


def test_refuses_inside_ssh(tmp_path):
    home = tmp_path / "home" / "alice"
    home.mkdir(parents=True)
    assert validate_workdir(home / ".ssh", home) is not None
    assert validate_workdir(home / ".ssh" / "keys", home) is not None
    assert validate_workdir(home / ".config" / "foo", home) is not None


def test_allows_dedicated_workspace(tmp_path):
    home = tmp_path / "home" / "alice"
    home.mkdir(parents=True)
    assert validate_workdir(home / "codriver-workspace", home) is None
    assert validate_workdir(home / "work" / "scratch", home) is None
    assert validate_workdir(tmp_path / "elsewhere", home) is None


def test_refuses_miscased_sensitive(tmp_path):
    # APFS is case-insensitive: ~/documents IS ~/Documents — refuse both.
    home = tmp_path / "home" / "alice"
    home.mkdir(parents=True)
    assert validate_workdir(home / "documents", home) is not None
    assert validate_workdir(home / "DOCUMENTS", home) is not None
    assert validate_workdir(home / ".SSH", home) is not None
    assert validate_workdir(home / ".Ssh" / "keys", home) is not None


def test_refuses_miscased_home_prefix(tmp_path):
    # User types their home path in the wrong case (~/home/ALICE) before a
    # sensitive subdir — realpath doesn't canonicalize case, so the casefolded
    # parent check must still refuse it.
    home = tmp_path / "home" / "alice"
    home.mkdir(parents=True)
    miscased_home = tmp_path / "home" / "ALICE"
    assert validate_workdir(miscased_home / "Documents", home) is not None


def _mute_loops(monkeypatch):
    # Keep the supervisor's per-iteration loop swap from creating real loops in
    # the test process; the loop fix itself is verified separately.
    from codriver import cli
    monkeypatch.setattr(cli.asyncio, "new_event_loop", lambda: None)
    monkeypatch.setattr(cli.asyncio, "set_event_loop", lambda *_a, **_k: None)
    monkeypatch.setattr(cli.time, "sleep", lambda *_a, **_k: None)


def test_supervisor_restarts_after_crash(monkeypatch):
    from codriver import cli, bot
    _mute_loops(monkeypatch)
    seq = iter([RuntimeError("boom"), None])  # crash once, then clean return
    calls = []

    def fake_main():
        calls.append(1)
        exc = next(seq)
        if exc:
            raise exc

    monkeypatch.setattr(bot, "main", fake_main)
    cli._supervise_bot()
    assert calls == [1, 1]  # it actually restarted, then stopped on clean exit


def test_supervisor_circuit_breaker(monkeypatch):
    from codriver import cli, bot
    _mute_loops(monkeypatch)
    calls = []

    def always_crash():
        calls.append(1)
        raise RuntimeError("boom")

    monkeypatch.setattr(bot, "main", always_crash)
    cli._supervise_bot()
    assert len(calls) == 6  # 5-in-60s breaker trips on the 6th crash
