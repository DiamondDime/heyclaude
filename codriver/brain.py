import json
import subprocess
from pathlib import Path

from .config import WORK_DIR, CLAUDE_TIMEOUT


def build_command(prompt: str, session_file: Path) -> list[str]:
    cmd = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "json",
        "--dangerously-skip-permissions",  # SAFE ONLY because cwd is the sandbox repo
    ]
    if session_file.exists():
        sid = session_file.read_text().strip()
        if sid:
            cmd += ["--resume", sid]
    return cmd


def ask_claude(prompt: str, session_file: Path = None) -> str:
    session_file = session_file or (WORK_DIR / ".codriver_session")
    cmd = build_command(prompt, session_file)
    proc = subprocess.run(
        cmd,
        cwd=str(WORK_DIR),
        capture_output=True,
        text=True,
        timeout=CLAUDE_TIMEOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "claude exited non-zero")
    data = json.loads(proc.stdout)
    if data.get("session_id"):
        session_file.write_text(data["session_id"])
    return data.get("result", "").strip()
