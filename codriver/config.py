import os
import shutil
import subprocess
from pathlib import Path


def _keychain_token() -> str:
    """Read the bot token from the macOS Keychain (off-disk, out of Claude's
    reachable path). Stored once with:

        security add-generic-password -s codriver-bot -a "$USER" -w '<token>'

    Returns "" on any platform/error so callers can fall back to env vars.
    """
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", "codriver-bot", "-w"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def _load_token() -> str:
    # Prefer an explicit env var (e.g. injected from a secret manager), but fall
    # back to the macOS Keychain so the token does NOT have to live in a `.env`
    # file adjacent to Claude's sandbox cwd, where a skip-permissions or
    # prompt-injected Claude could `cat ../.env` and exfiltrate it.
    return os.environ.get("TELEGRAM_BOT_TOKEN", "") or _keychain_token()


TOKEN = _load_token()
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", "0"))
WORK_DIR = Path(os.environ.get("CODRIVER_WORKDIR", str(Path.home() / "codriver" / "sandbox")))

# Optional OS-level confinement for Claude (finding #3). Off by default so the
# default path is unchanged; cwd alone is NOT a jail. When enabled, brain.py
# wraps each `claude` call in `sandbox-exec -f <profile>`.
SANDBOX_ENABLED = os.environ.get("CODRIVER_SANDBOX", "") == "1"
SANDBOX_PROFILE = Path(
    os.environ.get("CODRIVER_SANDBOX_PROFILE", str(Path(__file__).parent / "codriver.sb"))
)

# Default TTS voice: Samantha is actually installed on this Mac. Ava is NOT —
# `say` returns exit code 0 for a missing voice and silently uses the OS
# default, so we must pin to an installed voice and validate at runtime (tts.py).
DEFAULT_TTS_VOICE = os.environ.get("CODRIVER_VOICE", "Samantha")
DEFAULT_TTS_FALLBACK_VOICE = "Samantha"
# Backwards-compatible alias used elsewhere in the codebase.
VOICE = DEFAULT_TTS_VOICE

# Opus encode settings for Telegram voice notes (mono).
OPUS_BITRATE = "32k"
OPUS_CHANNELS = 1

# ffmpeg is the Homebrew build, not /usr/bin. Resolve via PATH, fall back to the
# known Homebrew location.
FFMPEG_PATH = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"

WHISPER_MODEL = os.environ.get("CODRIVER_WHISPER", "base.en")
SESSION_FILE = WORK_DIR / ".codriver_session"
CLAUDE_TIMEOUT = int(os.environ.get("CODRIVER_TIMEOUT", "600"))


def is_allowed(user_id: int, allowed: int = ALLOWED_USER_ID) -> bool:
    return user_id == allowed and allowed != 0
