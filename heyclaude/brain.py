import json
import logging
import shutil
import subprocess
from pathlib import Path

from .config import (
    WORK_DIR,
    CLAUDE_TIMEOUT,
    CLAUDE_EFFORT,
    CLAUDE_MODEL,
    SANDBOX_ENABLED,
    SANDBOX_PROFILE,
)

log = logging.getLogger("heyclaude")

# WARNING: `--dangerously-skip-permissions` gives Claude UNRESTRICTED access to
# the whole filesystem and network. `cwd=WORK_DIR` is only a working directory,
# NOT a jail — Claude can still read ~/.ssh, exfiltrate via curl, or rm -rf $HOME
# regardless of cwd. The single-user whitelist (config.is_allowed) is the only
# always-on boundary. Set HEYCLAUDE_SANDBOX=1 to additionally wrap each call in a
# macOS sandbox-exec profile (see heyclaude/heyclaude.sb).
FRESH_SESSION_NOTICE = "Heads up — I started a fresh session. "


# Typed failures so the bot can speak a SPECIFIC, actionable line instead of a
# generic "something broke" — the difference between the driver knowing to stop
# (usage limit won't clear for hours) and fruitlessly re-sending while driving.
class ClaudeUsageLimitError(RuntimeError):
    """Claude reported a usage/rate limit — persistent, won't clear on retry."""


class ClaudeAuthError(RuntimeError):
    """Claude is not signed in / not authenticated on this machine."""


# Substrings (lowercased) seen in claude's stderr / error result for each case.
_LIMIT_SIGNS = (
    "usage limit", "rate limit", "limit reached", "too many requests",
    "quota", "resets at",
)
_AUTH_SIGNS = (
    "not logged in", "not authenticated", "unauthorized", "invalid api key",
    "authentication failed", "claude login", "please log in", "please sign in",
)


def classify_claude_error(text: str | None):
    """Map a claude failure message to a typed error class, or None if unknown."""
    t = (text or "").lower()
    if any(s in t for s in _LIMIT_SIGNS):
        return ClaudeUsageLimitError
    if any(s in t for s in _AUTH_SIGNS):
        return ClaudeAuthError
    return None


def _raise_claude_error(message: str) -> None:
    cls = classify_claude_error(message) or RuntimeError
    raise cls(message or "claude exited non-zero")


def build_command(prompt: str, session_file: Path, allow_resume: bool = True,
                  effort: str | None = None, model: str | None = None) -> list[str]:
    cmd = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "json",
        "--dangerously-skip-permissions",  # cwd is NOT a jail — see module docstring
    ]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]
    if allow_resume and session_file.exists():
        sid = session_file.read_text().strip()
        if sid:
            cmd += ["--resume", sid]
    return cmd


def _maybe_sandbox(cmd: list[str]) -> list[str]:
    """Optionally confine Claude with a macOS sandbox-exec profile.

    Opt-in via HEYCLAUDE_SANDBOX=1. Off by default so the default path is
    unchanged; validate the profile against a real `claude` run before relying
    on it, since over-tight deny rules can break Claude mid-drive.
    """
    if (
        SANDBOX_ENABLED
        and SANDBOX_PROFILE.exists()
        and shutil.which("sandbox-exec")
    ):
        return [
            "sandbox-exec",
            "-D",
            f"WORK={WORK_DIR}",
            "-D",
            f"HOME={Path.home()}",
            "-f",
            str(SANDBOX_PROFILE),
        ] + cmd
    return cmd


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        _maybe_sandbox(cmd),
        cwd=str(WORK_DIR),
        capture_output=True,
        text=True,
        timeout=CLAUDE_TIMEOUT,
    )


def ask_claude(prompt: str, session_file: Path | None = None,
               effort: str | None = None, model: str | None = None) -> str:
    session_file = session_file or (WORK_DIR / ".heyclaude_session")
    effort = effort or CLAUDE_EFFORT
    model = model or CLAUDE_MODEL

    had_session = session_file.exists() and bool(session_file.read_text().strip())
    proc = _run(build_command(prompt, session_file, allow_resume=True, effort=effort, model=model))

    fresh = False
    if proc.returncode != 0 and had_session:
        # A stale/invalid/corrupt session id makes `--resume` fail every turn and
        # would brick the bot for the rest of the drive (the session file can't be
        # deleted by hand while driving). Drop it and retry once with a fresh
        # session so the next turns can succeed.
        log.warning("resume failed (%s); dropping session and retrying fresh", session_file)
        try:
            session_file.unlink()
        except FileNotFoundError:
            pass
        proc = _run(build_command(prompt, session_file, allow_resume=False, effort=effort, model=model))
        fresh = True

    if proc.returncode != 0:
        _raise_claude_error(proc.stderr.strip())

    try:
        data = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError) as e:
        raise RuntimeError(f"claude returned unparseable output: {e}") from e

    # claude can exit 0 yet still report a logical error (e.g. max turns / error
    # subtypes). Don't speak that error text back as if it were a normal answer.
    # NOTE: do NOT run the usage-limit/auth classifier on this result body — it's
    # Claude's free-form task narration, which routinely quotes a sub-command's
    # "unauthorized"/"rate limit" and would misfire into a wrong spoken warning.
    # Classification is reserved for the stderr control-plane path above.
    if data.get("is_error"):
        raise RuntimeError(
            data.get("result") or data.get("subtype") or "claude reported an error"
        )

    if data.get("session_id"):
        session_file.write_text(data["session_id"])

    result = data.get("result", "").strip()
    if fresh:
        result = FRESH_SESSION_NOTICE + result
    return result


def preflight(model: str | None = None, timeout: int = 90) -> tuple[bool, str]:
    """Run one trivial claude turn to prove the CLI is usable BEFORE a drive.

    Catches the three failures that otherwise only show up as every-turn-fails
    on the road: `claude` missing from PATH, not signed in, or a build that
    rejects the `--model` / `--effort` flags (which would brick every turn).
    Returns (ok, human message). Uses `--effort low` so the check is quick —
    we're testing that the flag is ACCEPTED, not the configured depth.
    """
    if not shutil.which("claude"):
        return False, "the `claude` CLI is not on your PATH (install Claude Code)"
    model = model or CLAUDE_MODEL
    cmd = [
        "claude", "-p", "Reply with exactly the word: ready",
        "--output-format", "json", "--dangerously-skip-permissions",
        "--model", model, "--effort", "low",
    ]
    try:
        proc = subprocess.run(
            cmd, cwd=str(WORK_DIR), capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError:
        return False, "the `claude` CLI is not on your PATH (install Claude Code)"
    except subprocess.TimeoutExpired:
        return False, f"claude did not respond within {timeout}s"
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        cls = classify_claude_error(err)
        if cls is ClaudeAuthError:
            return False, "claude is not signed in — run `claude` once and log in"
        return False, err or "claude exited non-zero (check `claude` works in a terminal)"
    try:
        data = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        return False, "claude returned unexpected output"
    if data.get("is_error"):
        return False, str(data.get("result") or "claude reported an error")
    return True, "signed in and responding"
