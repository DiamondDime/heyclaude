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

log = logging.getLogger("codriver")

# WARNING: `--dangerously-skip-permissions` gives Claude UNRESTRICTED access to
# the whole filesystem and network. `cwd=WORK_DIR` is only a working directory,
# NOT a jail — Claude can still read ~/.ssh, exfiltrate via curl, or rm -rf $HOME
# regardless of cwd. The single-user whitelist (config.is_allowed) is the only
# always-on boundary. Set CODRIVER_SANDBOX=1 to additionally wrap each call in a
# macOS sandbox-exec profile (see codriver/codriver.sb).
FRESH_SESSION_NOTICE = "Heads up — I started a fresh session. "


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

    Opt-in via CODRIVER_SANDBOX=1. Off by default so the default path is
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
    session_file = session_file or (WORK_DIR / ".codriver_session")
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
        raise RuntimeError(proc.stderr.strip() or "claude exited non-zero")

    try:
        data = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError) as e:
        raise RuntimeError(f"claude returned unparseable output: {e}") from e

    # claude can exit 0 yet still report a logical error (e.g. max turns / error
    # subtypes). Don't speak that error text back as if it were a normal answer.
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
