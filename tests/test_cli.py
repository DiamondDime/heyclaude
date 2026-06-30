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
