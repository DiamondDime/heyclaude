import os
import shutil
import subprocess
import tomllib
from pathlib import Path


def _int_env(name: str, default: int) -> int:
    """Parse an int env var defensively. A blank or non-numeric value (e.g.
    `ALLOWED_USER_ID=` left in .env) returns the default instead of crashing the
    whole process at import time with an opaque ValueError."""
    try:
        return int(os.environ.get(name, "").strip())
    except (ValueError, AttributeError):
        return default


def _keychain_token() -> str:
    """Read the bot token from the macOS Keychain (off-disk, out of Claude's
    reachable path). Stored once with:

        security add-generic-password -s heyclaude-bot -a "$USER" -w '<token>'

    Returns "" on any platform/error so callers can fall back to env vars.
    """
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", "heyclaude-bot", "-w"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


# config.toml lives OUTSIDE the repo (under ~/.config/heyclaude) so a
# skip-permissions Claude running in the workspace cannot read it. The dir is
# overridable via HEYCLAUDE_CONFIG_DIR for tests / non-default installs.
CONFIG_DIR = Path(
    os.environ.get("HEYCLAUDE_CONFIG_DIR", str(Path.home() / ".config" / "heyclaude"))
)
CONFIG_FILE = CONFIG_DIR / "config.toml"


def _load() -> dict:
    """Load config.toml as a dict. Returns {} when the file is missing or
    malformed so the bot keeps running on env vars + defaults alone — the
    currently-deployed prototype (no TOML) is unaffected. Never raises."""
    try:
        with open(CONFIG_FILE, "rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError, ValueError):
        return {}


_CONFIG = _load()


def _section(*keys: str) -> dict:
    """Walk nested TOML tables, returning {} if any level is missing or not a
    table. Defensive against partial / hand-edited config files."""
    node = _CONFIG
    for key in keys:
        if not isinstance(node, dict):
            return {}
        node = node.get(key, {})
    return node if isinstance(node, dict) else {}


def _toml_str(value, default: str = "") -> str:
    """Coerce a TOML value to a stripped string, falling back to default for
    missing / non-string values."""
    return str(value).strip() if isinstance(value, str) else default


_telegram = _section("telegram")
_tts = _section("tts")
_tts_eleven = _section("tts", "elevenlabs")
_tts_say = _section("tts", "say")
_claude = _section("claude")


def _load_token() -> str:
    # Precedence: env TELEGRAM_BOT_TOKEN > [telegram].bot_token > "", then fall
    # back to the macOS Keychain so the token need not live on disk adjacent to
    # Claude's sandbox cwd, where a skip-permissions or prompt-injected Claude
    # could read it and exfiltrate.
    env = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    return (env or _toml_str(_telegram.get("bot_token"))) or _keychain_token()


TOKEN = _load_token()

# ALLOWED_USER_ID: env > [telegram].allowed_user_id > 0.
_toml_allowed = _telegram.get("allowed_user_id", 0)
try:
    _toml_allowed = int(_toml_allowed)
except (TypeError, ValueError):
    _toml_allowed = 0
ALLOWED_USER_ID = _int_env("ALLOWED_USER_ID", _toml_allowed)

# WORK_DIR: env HEYCLAUDE_WORKDIR > [claude].workdir > ~/heyclaude-workspace.
_default_workdir = str(Path.home() / "heyclaude-workspace")
WORK_DIR = Path(
    os.environ.get("HEYCLAUDE_WORKDIR", "")
    or _toml_str(_claude.get("workdir"), _default_workdir)
)

# Optional OS-level confinement for Claude (finding #3). Off by default so the
# default path is unchanged; cwd alone is NOT a jail. When enabled, brain.py
# wraps each `claude` call in `sandbox-exec -f <profile>`.
SANDBOX_ENABLED = os.environ.get("HEYCLAUDE_SANDBOX", "") == "1"
SANDBOX_PROFILE = Path(
    os.environ.get("HEYCLAUDE_SANDBOX_PROFILE", str(Path(__file__).parent / "heyclaude.sb"))
)

# Default TTS voice: Samantha is actually installed on this Mac. `say` returns
# exit code 0 for a missing voice and silently uses the OS default, so we pin to
# an installed voice and validate at runtime (tts.py).
# Precedence: env HEYCLAUDE_VOICE > [tts.say].voice > "Samantha".
DEFAULT_TTS_VOICE = os.environ.get("HEYCLAUDE_VOICE", "") or _toml_str(
    _tts_say.get("voice"), "Samantha"
)
DEFAULT_TTS_FALLBACK_VOICE = "Samantha"
# Backwards-compatible alias used elsewhere in the codebase.
VOICE = DEFAULT_TTS_VOICE

# Opus encode settings for Telegram voice notes (mono).
OPUS_BITRATE = "32k"
OPUS_CHANNELS = 1

# TTS backend: "say" (local macOS, robotic, free) or "elevenlabs" (cloud, human).
# Precedence: env HEYCLAUDE_TTS_BACKEND > [tts].backend > "say".
TTS_BACKEND = (
    os.environ.get("HEYCLAUDE_TTS_BACKEND", "") or _toml_str(_tts.get("backend"), "say")
).strip().lower()
# Precedence: env ELEVENLABS_API_KEY > [tts.elevenlabs].api_key > "".
ELEVENLABS_API_KEY = (
    os.environ.get("ELEVENLABS_API_KEY", "") or _toml_str(_tts_eleven.get("api_key"))
).strip()
# Default voice "Chris" (a premade voice every account has). NOTE: free-tier API
# users can only use premade/owned voices, not shared Voice Library voices.
# Precedence: env ELEVENLABS_VOICE_ID > [tts.elevenlabs].voice_id > default.
ELEVENLABS_VOICE_ID = (
    os.environ.get("ELEVENLABS_VOICE_ID", "")
    or _toml_str(_tts_eleven.get("voice_id"), "iP95p4xoKVk53GoZ742B")
).strip()
# turbo_v2_5 = low latency + natural; multilingual_v2 = highest quality, slower.
# Precedence: env ELEVENLABS_MODEL > [tts.elevenlabs].model > default.
ELEVENLABS_MODEL = (
    os.environ.get("ELEVENLABS_MODEL", "")
    or _toml_str(_tts_eleven.get("model"), "eleven_turbo_v2_5")
).strip()

# ffmpeg is the Homebrew build, not /usr/bin. Resolve via PATH, fall back to the
# known Homebrew location.
FFMPEG_PATH = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"

WHISPER_MODEL = os.environ.get("HEYCLAUDE_WHISPER", "base.en")
SESSION_FILE = WORK_DIR / ".heyclaude_session"
# CLAUDE_TIMEOUT: env HEYCLAUDE_TIMEOUT > [claude].timeout > 600.
_toml_timeout = _claude.get("timeout", 600)
try:
    _toml_timeout = int(_toml_timeout)
except (TypeError, ValueError):
    _toml_timeout = 600
CLAUDE_TIMEOUT = _int_env("HEYCLAUDE_TIMEOUT", _toml_timeout)

# Reasoning effort for each claude turn. Valid CLI levels: low, medium, high,
# xhigh, max (an unknown value is ignored by the CLI, falling back to default).
# Precedence: env HEYCLAUDE_EFFORT > [claude].effort > "xhigh".
CLAUDE_EFFORT = (
    os.environ.get("HEYCLAUDE_EFFORT", "") or _toml_str(_claude.get("effort"), "xhigh")
).strip().lower()
# Model for each claude turn. Precedence: env HEYCLAUDE_MODEL > [claude].model >
# "claude-opus-4-8". These are runtime DEFAULTS; the bot can switch live (runtime.py).
CLAUDE_MODEL = (
    os.environ.get("HEYCLAUDE_MODEL", "") or _toml_str(_claude.get("model"), "claude-opus-4-8")
).strip()


def is_allowed(user_id: int, allowed: int = ALLOWED_USER_ID) -> bool:
    return user_id == allowed and allowed != 0
