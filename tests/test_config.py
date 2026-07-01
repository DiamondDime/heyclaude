from heyclaude.config import is_allowed


def test_allows_owner():
    assert is_allowed(12345, allowed=12345) is True


def test_blocks_stranger():
    assert is_allowed(99999, allowed=12345) is False


def test_zero_allowed_blocks_everyone():
    assert is_allowed(0, allowed=0) is False
